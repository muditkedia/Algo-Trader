"""StreamingCandleSource - locally built candles behind the v2 source seam.

This is the websocket migration in ONE seam. The v2 architecture already
funnels every fetch through :class:`MarketDataSource`; this source answers
candle requests **from the LocalCandleEngine's finalized candles in memory**
instead of the network. Everything above it - scheduler determinism,
MarketState ownership, freshness, completion-driven scanning, the engine, the
scanner, the dashboard - runs unchanged, which is exactly what the v2 design
promised: "a streaming source ... is the seam a websocket transport slots
into without the scanner noticing".

The HISTORICAL provider is retained behind this source as a fallback and
infrastructure service, used ONLY when the requested window is not covered by
clean local candles:

* **startup seeding** - the scheduler's first pass asks for the incremental
  window since the store's last bar; that window predates websocket coverage,
  so it delegates to the historical API once, warming indicators;
* **gap repair** - after a websocket outage the affected buckets are tainted
  or missing; exactly that sub-window is delegated, never the whole history;
* **validation mode** (``validate=True``) - locally built candles are
  compared side-by-side against the historical API and mismatches are logged
  and counted, without changing what is served.

Once coverage is established, requests are served from memory at memory speed
with ZERO provider calls - routine historical polling is gone, and a
300-symbol pass completes in a handful of engine polls instead of minutes.
"""

from __future__ import annotations

import time
from typing import Dict, Iterable, Optional

import pandas as pd

from algo.core.logging import get_logger
from algo.marketdata.capabilities import (
    ProviderCapabilities, SMARTAPI_MAX_DAYS,
)
from algo.marketdata.localcandles import LocalCandleEngine
from algo.marketdata.source import MarketDataSource, Quote

logger = get_logger("marketdata.streaming")

#: The streaming source's declared profile. No candle rate limits: in-memory
#: serving costs nothing, and delegated historical calls are paced by the
#: wrapped provider's own throttle (it is built WITHOUT transport-managed
#: pacing precisely so it self-paces on this path). Quotes come from the tick
#: stream, so one "request" serves the whole watchlist.
STREAMING_CAPABILITIES = ProviderCapabilities(
    name="smartapi-ws",
    candle_symbols_per_request=1,
    quote_symbols_per_request=1000,
    max_days_per_request=SMARTAPI_MAX_DAYS,
    candle_limits=(),
    quote_limits=(),
    max_concurrent_requests=1,
    supports_streaming=True,
    verified=True,
)


class StreamingCandleSource(MarketDataSource):
    """Serves candles from the LocalCandleEngine; delegates gaps to the
    historical source. The ONLY candle constructor behind it is the engine."""

    name = "smartapi-ws"

    def __init__(self, feed, engine: LocalCandleEngine,
                 fallback: MarketDataSource,
                 validate: bool = False,
                 stale_after_s: float = 30.0,
                 clock=None) -> None:
        self.feed = feed
        self.engine = engine
        self.fallback = fallback
        self.validate = bool(validate)
        #: seconds without any packet (while started, during trading) before
        #: the source reports DEGRADED rather than OK
        self.stale_after_s = float(stale_after_s)
        self.clock = clock
        feed.set_dispatcher(engine.on_tick)
        # counters: the dashboard's proof that polling is actually gone
        self.served_local = 0
        self.delegated = 0
        self.validated = 0
        self.validation_mismatches = 0

    # ------------------------------------------------------------ lifecycle

    def start(self, symbols: Iterable[str]) -> int:
        return self.feed.start(symbols)

    def stop(self) -> None:
        self.feed.stop()

    def resubscribe(self, symbols: Iterable[str]) -> int:
        """Universe changed: resync the candle engine (volume baselines do
        not carry across subscriptions) and re-subscribe the feed."""
        self.engine.mark_gap()
        return self.feed.resubscribe(symbols)

    # -------------------------------------------------------------- candles

    def fetch_candles(self, symbol: str, timeframe: str, start,
                      end) -> pd.DataFrame:
        now_s = self._now_epoch()
        self.engine.finalize_before(now_s)
        start_s = _epoch(start)
        end_s = min(_epoch(end), now_s)

        if timeframe not in self.engine.timeframes:
            # a timeframe the engine was not built for (e.g. 1d): honest
            # delegation beats a wrong local bar (SUPPORTED_TIMEFRAMES)
            self.delegated += 1
            return self.fallback.fetch_candles(symbol, timeframe, start, end)

        gap = self.engine.uncovered_before(symbol, timeframe, start_s, end_s,
                                           outages=self._outages())
        local = self.engine.frame(symbol, timeframe, start_s, end_s)

        if gap is None:
            self.served_local += 1
            if self.validate and not local.empty:
                self._validate(symbol, timeframe, local)
            return local

        gap_start, gap_end = gap
        self.delegated += 1
        logger.debug("%s %s: delegating %s..%s to %s (seed/gap repair)",
                     symbol, timeframe,
                     pd.Timestamp(gap_start, unit="s", tz="UTC"),
                     pd.Timestamp(gap_end, unit="s", tz="UTC"),
                     self.fallback.name)
        hist = self.fallback.fetch_candles(
            symbol, timeframe,
            pd.Timestamp(gap_start, unit="s", tz="UTC"),
            pd.Timestamp(gap_end, unit="s", tz="UTC"))
        if local.empty:
            return hist
        if hist is None or hist.empty:
            return local
        # historical fills the gap; local wins where both hold a bucket
        # (local candles are the validated primary once clean)
        merged = pd.concat([hist, local], ignore_index=True)
        merged = merged.drop_duplicates(subset="date", keep="last")
        return merged.sort_values("date").reset_index(drop=True)

    def _validate(self, symbol: str, timeframe: str,
                  local: pd.DataFrame) -> None:
        """Side-by-side comparison against the historical API (config-gated).

        Validation is bounded to the most recent finalized candle so enabling
        it costs one historical request per served window, not a backfill.
        """
        last = local.iloc[-1]
        try:
            hist = self.fallback.fetch_candles(symbol, timeframe,
                                               last["date"], last["date"])
        except Exception as exc:      # validation must never break serving
            logger.debug("validation fetch failed for %s %s: %s",
                         symbol, timeframe, exc)
            return
        self.validated += 1
        row = hist[hist["date"] == last["date"]] if hist is not None else None
        if row is None or row.empty:
            return
        row = row.iloc[0]
        diffs = {f: float(last[f]) - float(row[f])
                 for f in ("open", "high", "low", "close", "volume")}
        if any(abs(d) > 0.005 for f, d in diffs.items() if f != "volume") \
                or abs(diffs["volume"]) >= 1:
            self.validation_mismatches += 1
            logger.warning("VALIDATION MISMATCH %s %s @ %s: %s",
                           symbol, timeframe, last["date"], diffs)

    # --------------------------------------------------------------- quotes

    def fetch_quotes(self, symbols: Iterable[str]) -> Dict[str, Quote]:
        """Marks from the tick stream - no REST call, always current."""
        out: Dict[str, Quote] = {}
        for symbol in symbols:
            entry = self.feed.last_price.get(symbol)
            if entry is None:
                continue
            price, ts = entry
            if price and price > 0:
                out[symbol] = Quote(symbol=symbol, price=float(price),
                                    ts=pd.Timestamp(ts, unit="s", tz="UTC"))
        return out

    # ------------------------------------------------------------- plumbing

    @property
    def capabilities(self) -> ProviderCapabilities:
        return STREAMING_CAPABILITIES

    def unavailable_reason(self, symbol: str,
                           timeframe: str) -> Optional[str]:
        # token resolution and timeframe support are the fallback's to judge:
        # any timeframe it can serve, this source can serve (locally or by
        # delegation), and both resolve symbols through the same master
        return self.fallback.unavailable_reason(symbol, timeframe)

    def is_rate_limited(self, error: Exception) -> bool:
        return self.fallback.is_rate_limited(error)

    def status(self) -> str:
        """OK while packets flow; DEGRADED when the socket is silent or down
        (the fallback still serves, so the system is degraded, not offline)."""
        if not self.feed.started:
            return self.fallback.status()
        if self.feed.open_outage_since() is not None:
            return "DEGRADED"
        age = self.feed.seconds_since_packet()
        in_session = True
        if self.clock is not None:
            try:
                in_session = self.clock.is_open(self.clock.now())
            except Exception:
                in_session = True
        if in_session and age is not None and age > self.stale_after_s:
            return "DEGRADED"
        return "OK"

    def mapping_report(self):
        return self.fallback.mapping_report()

    def _outages(self):
        return list(self.feed.outages)

    def _now_epoch(self) -> float:
        if self.clock is not None:
            try:
                return self.clock.now().timestamp()
            except Exception:
                pass
        return time.time()

    def snapshot(self) -> dict:
        return {
            "feed": self.feed.snapshot(),
            "engine": self.engine.snapshot(),
            "served_local": self.served_local,
            "delegated_to_historical": self.delegated,
            "validated": self.validated,
            "validation_mismatches": self.validation_mismatches,
        }


def _epoch(ts) -> float:
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.timestamp()
