"""MarketDataSource - the seam a provider is swapped at.

``DataProvider`` (algo.data.providers.base) answers "give me bars for this
window" and is shared with the research/backfill path. A LIVE source needs two
more things that only matter when something is waiting on the answer:

* **capabilities** - what may be batched, and at what rate. The scheduler plans
  against these, so they must be declared rather than discovered by failing.
* **quotes** - a cheap current price for the whole watchlist, which on a
  provider with a bulk endpoint costs a fraction of a candle request per
  symbol and on one without simply is not offered.

Everything else stays on ``DataProvider``, and :class:`ProviderSource` adapts
any existing one - synthetic, CSV, Kotak, SmartAPI - so the entire provider
ecosystem plugs into the v2 subsystem unchanged. Swapping SmartAPI REST for
Zerodha, Polygon, a replay engine, or a websocket transport is implementing
this interface; no scanner, strategy or dashboard code moves.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

import pandas as pd

from algo.core.logging import get_logger
from algo.marketdata.capabilities import (
    ProviderCapabilities, UNLIMITED_CAPABILITIES,
)

logger = get_logger("marketdata.source")


def _positive(value) -> bool:
    """A usable price. A quote of 0, None or "" is absence, not a price."""
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class Quote:
    """A last-traded price and when it was observed.

    Display and position marking ONLY. No decision reads a quote: stops,
    targets and entries all evaluate completed bars, exactly as they were
    measured. Keeping quotes in their own type makes that boundary visible -
    a quote cannot be mistaken for a bar close by accident.
    """

    symbol: str
    price: float
    ts: Optional[pd.Timestamp] = None

    def valid(self) -> bool:
        return self.price is not None and self.price > 0


class MarketDataSource(ABC):
    """Contract for a live market-data source."""

    name: str = "source"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return UNLIMITED_CAPABILITIES

    @abstractmethod
    def fetch_candles(self, symbol: str, timeframe: str, start,
                      end) -> pd.DataFrame:
        """Canonical OHLCV bars in ``[start, end]`` (may be empty)."""

    def fetch_quotes(self, symbols: Iterable[str]) -> Dict[str, Quote]:
        """Latest prices for ``symbols``. Empty when unsupported.

        Implementations that have a bulk endpoint must declare its size in
        ``capabilities.quote_symbols_per_request``; the caller batches to that
        size and never asks for more in one call.
        """
        return {}

    def unavailable_reason(self, symbol: str,
                           timeframe: str) -> Optional[str]:
        """Why this request cannot be served AT ALL, or None.

        "No bars in this window" is a normal, quiet answer. "This symbol has no
        instrument token" is an operator problem. A source that can tell them
        apart up front says so here, and the two are reported under different
        statuses instead of both looking like a quiet market (D-038).
        """
        return None

    def is_rate_limited(self, error: Exception) -> bool:
        """Is this exception the provider saying "too fast"?

        The transport must distinguish rate limiting from a real error without
        knowing the provider's dialect - SmartAPI reports it as a JSON parse
        failure, which looks exactly like corrupt data unless you know better.
        """
        return False

    def status(self) -> str:
        """OK / DEGRADED / OFFLINE, from the source's own knowledge."""
        return "OK"

    def mapping_report(self):
        """Symbol -> instrument resolution, when the source has a master."""
        return None


class ProviderSource(MarketDataSource):
    """Adapts any ``DataProvider`` into a :class:`MarketDataSource`.

    This is what keeps the v2 subsystem provider-agnostic in practice rather
    than only in principle: every provider already written for the platform
    works here with no change, and a new one only implements ``DataProvider``.
    """

    def __init__(self, provider, capabilities: Optional[ProviderCapabilities]
                 = None, name: Optional[str] = None) -> None:
        self.provider = provider
        self._capabilities = capabilities or UNLIMITED_CAPABILITIES
        self.name = name or getattr(provider, "name", "provider")

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def fetch_candles(self, symbol: str, timeframe: str, start,
                      end) -> pd.DataFrame:
        return self.provider.fetch_ohlcv(symbol, timeframe, start, end)

    def fetch_quotes(self, symbols: Iterable[str]) -> Dict[str, Quote]:
        """One bulk call when the provider has that route, else per symbol.

        The bulk path is taken ONLY when the provider actually implements
        ``fetch_quotes``; the fallback then sends N requests for N symbols and
        is honest about it. The dangerous middle case - looping internally
        while the scheduler believes one request was sent - cannot arise,
        because the batch size the caller splits on comes from the same
        capability declaration that decides which path runs.
        """
        symbols = list(symbols)
        now = pd.Timestamp.now(tz="UTC")
        bulk = getattr(self.provider, "fetch_quotes", None)
        if bulk is not None and self._capabilities.quotes_batchable():
            try:
                prices = bulk(symbols) or {}
            except Exception as exc:       # marking must never break a cycle
                logger.debug("bulk quote failed for %d symbol(s): %s",
                             len(symbols), exc)
                return {}
            return {s: Quote(symbol=s, price=float(p), ts=now)
                    for s, p in prices.items()
                    if _positive(p)}

        getter = getattr(self.provider, "latest_quote", None)
        if getter is None:
            return {}
        out: Dict[str, Quote] = {}
        for symbol in symbols:
            try:
                data = getter(symbol)
            except Exception as exc:       # marking must never break a cycle
                logger.debug("quote failed for %s: %s", symbol, exc)
                continue
            price = data.get("ltp") if isinstance(data, dict) else None
            if _positive(price):
                out[symbol] = Quote(symbol=symbol, price=float(price), ts=now)
        return out

    def unavailable_reason(self, symbol: str,
                           timeframe: str) -> Optional[str]:
        reason = getattr(self.provider, "unavailable_reason", None)
        return reason(symbol, timeframe) if reason is not None else None

    def is_rate_limited(self, error: Exception) -> bool:
        checker = getattr(self.provider, "_is_rate_limited", None)
        if checker is not None:
            try:
                return bool(checker(error))
            except Exception:
                return False
        return False

    def mapping_report(self):
        instruments = getattr(self.provider, "instruments", None)
        if instruments is None or not hasattr(instruments, "resolve_many"):
            return None
        return instruments

    def list_symbols(self) -> List[str]:
        lister = getattr(self.provider, "list_symbols", None)
        return lister() if lister is not None else []


#: Provider name -> verified capability declaration. A provider absent from
#: this table gets the unlimited/no-batching profile, which is the SAFE
#: default: it plans one symbol per request and applies no throttle claims the
#: provider never made.
_CAPABILITIES_BY_NAME = {}


def for_provider(provider, name: Optional[str] = None) -> "ProviderSource":
    """Wrap a ``DataProvider`` with the capability profile it was VERIFIED to
    have. This is the one place a provider becomes a live source.

    Deliberately keyed on the provider's declared name rather than its class,
    so a subclass or a test double that identifies as ``smartapi`` is planned
    for exactly like the real thing - and anything else gets the conservative
    profile instead of inheriting limits it may not share.
    """
    from algo.marketdata.capabilities import SMARTAPI_CAPABILITIES

    if not _CAPABILITIES_BY_NAME:
        _CAPABILITIES_BY_NAME["smartapi"] = SMARTAPI_CAPABILITIES
    key = name or getattr(provider, "name", "") or "provider"
    caps = _CAPABILITIES_BY_NAME.get(key, UNLIMITED_CAPABILITIES)
    if caps is UNLIMITED_CAPABILITIES and key != "local":
        logger.info("provider %r has no verified capability profile - "
                    "planning one symbol per request with no batching", key)
    return ProviderSource(provider, capabilities=caps, name=key)


class NullSource(MarketDataSource):
    """No provider wired. Serves nothing, and says so.

    Offline paper trading runs on this: the store's existing candles are still
    served by MarketState, but every fetch is refused with a reason rather than
    returning an empty frame that would read as "the market is quiet".
    """

    name = "offline"

    def fetch_candles(self, symbol: str, timeframe: str, start,
                      end) -> pd.DataFrame:
        from algo.data.ohlcv import OHLCV_COLUMNS
        return pd.DataFrame(columns=list(OHLCV_COLUMNS))

    def unavailable_reason(self, symbol: str,
                           timeframe: str) -> Optional[str]:
        return "no market-data provider wired (offline)"

    def status(self) -> str:
        return "OFFLINE"
