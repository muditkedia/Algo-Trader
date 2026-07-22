"""MarketState - the single runtime truth about the market.

Every consumer - scanner, strategies, trade manager, risk engine, dashboard -
reads this object and nothing else. It is the reason none of them can tell REST
from websocket from replay, and the reason two panels cannot disagree about
whether a price is stale: there is one answer because there is one place that
computes it.

What it holds (and therefore what nothing else may recompute):

    candles              per symbol/timeframe, hot and bounded
    latest prices        with the bar time they belong to, never separated
    freshness            candle-based, delegated to the frozen module
    symbol health        per-symbol success/failure, so 3 bad symbols cost 3
    timeframe health     per-timeframe fetch outcome
    last updates         when candles actually ARRIVED, per timeframe
    provider latency     rolling p50/p95 of real request times
    provider status      OK / DEGRADED / OFFLINE, from observed behaviour
    request queue        depth and composition (set by the service)

**Durability vs speed.** The parquet store stays the durable record; this holds
a validated hot cache over it. The cache is keyed on the file's
``(mtime_ns, size)``, so a write by anything else - the history downloader, a
test, a second process - is picked up on the next read instead of being shadowed
by a stale copy. That check is one ``stat`` call (microseconds) against a full
parquet read (milliseconds), which is where the per-symbol cost of the old
read-everything-every-cycle path went.

**Freshness is candle-based** and stays that way (D-039): it is measured
against the bar the exchange should have completed, in bars, never against the
wall clock and never against an exporter's write time. This module delegates to
``algo.trading.freshness`` rather than reimplementing it - one definition.
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

import pandas as pd

from algo.core.logging import get_logger
from algo.data import ohlcv
from algo.data.store import MarketDataStore

logger = get_logger("marketdata.state")

# NB: freshness and the clock live under algo.trading, which is a HIGHER layer
# than this one - importing them at module load would make the market-data
# subsystem depend on the trading package's import graph (engine included) and
# creates a cycle when marketdata is imported first. They are pure candle math
# with no trading state, so they are imported lazily inside the one method that
# uses them. The layering rule stands: nothing here loads trading at import.

#: Provider-level status vocabulary (distinct from per-symbol health).
OK = "OK"
DEGRADED = "DEGRADED"
OFFLINE = "OFFLINE"
PENDING = "PENDING"

#: Per-symbol health vocabulary.
H_OK = "OK"
H_DEGRADED = "DEGRADED"        # some failures, still serving
H_FAILED = "FAILED"            # consecutive failures past the threshold
H_UNAVAILABLE = "UNAVAILABLE"  # cannot be addressed at all (no token, etc.)


@dataclass
class SymbolHealth:
    """One symbol's fetch record. Isolation lives here: a symbol that keeps
    failing is marked and SKIPPED, never escalated into a feed-wide fault."""

    symbol: str
    status: str = H_OK
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_error: str = ""
    last_success: Optional[pd.Timestamp] = None
    last_attempt: Optional[pd.Timestamp] = None
    rows_added: int = 0

    @property
    def usable(self) -> bool:
        """May this symbol still be scanned? Degraded yes, failed no.

        A symbol with stored history and a transient fetch failure is still
        tradeable on the bars it has - refusing to scan it would turn one
        provider hiccup into a lost opportunity. Only a symbol that cannot be
        addressed, or has failed repeatedly, is set aside.
        """
        return self.status in (H_OK, H_DEGRADED)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "status": self.status,
            "consecutive_failures": self.consecutive_failures,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
            "last_error": self.last_error[:200],
            "last_success": (None if self.last_success is None
                             else str(self.last_success)),
        }


@dataclass
class TimeframeHealth:
    """One timeframe's aggregate fetch outcome for the current session."""

    timeframe: str
    requested: int = 0
    served: int = 0
    failed: int = 0
    rows_added: int = 0
    last_success: Optional[pd.Timestamp] = None
    last_attempt: Optional[pd.Timestamp] = None
    #: symbols the scheduler still owes a bar for (due but not yet served)
    pending: int = 0
    headline: str = ""

    @property
    def ok(self) -> bool:
        """Did the last pass serve the timeframe at all?

        ``failed`` alone is not a fault - three unreachable symbols out of 99
        is a data-quality condition, handled per symbol. A timeframe is only
        unhealthy when nothing at all could be served while work was pending.
        """
        if not self.requested:
            return True
        return self.served > 0 or self.failed == 0

    def to_dict(self) -> dict:
        return {
            "timeframe": self.timeframe, "requested": self.requested,
            "served": self.served, "failed": self.failed,
            "rows_added": self.rows_added, "pending": self.pending,
            "ok": self.ok, "headline": self.headline,
            "last_success": (None if self.last_success is None
                             else str(self.last_success)),
        }


@dataclass
class _Cached:
    frame: pd.DataFrame
    mtime_ns: int
    size: int
    loaded_at: float = field(default_factory=time.monotonic)


class MarketState:
    """The authoritative runtime market picture. Read by everything."""

    def __init__(self, store: MarketDataStore, symbols=(), timeframes=(),
                 history_bars: int = 1600,
                 stale_tolerance_bars: int = 1,
                 max_cached_series: int = 4000,
                 failure_threshold: int = 3,
                 latency_window: int = 200) -> None:
        self.store = store
        self.symbols: List[str] = list(symbols)
        self.timeframes: List[str] = list(timeframes)
        self.history_bars = int(history_bars)
        self.stale_tolerance_bars = int(stale_tolerance_bars)
        #: consecutive failures before a symbol is set aside as FAILED
        self.failure_threshold = int(failure_threshold)

        #: (symbol, timeframe) -> _Cached, LRU-bounded
        self._cache: "OrderedDict[Tuple[str, str], _Cached]" = OrderedDict()
        self.max_cached_series = int(max_cached_series)

        self.symbol_health: Dict[str, SymbolHealth] = {}
        self.timeframe_health: Dict[str, TimeframeHealth] = {}
        #: when candles were last actually ADDED, per timeframe. "A request
        #: completed" is NOT evidence the feed works (D-038); rows arriving is.
        self.last_update: Dict[str, pd.Timestamp] = {}

        #: live quotes for display/marking only. Never read by a decision.
        self.quotes: Dict[str, float] = {}
        self.quotes_ts: Optional[pd.Timestamp] = None

        self.provider_name: str = ""
        self.provider_status: str = PENDING
        self.provider_detail: str = ""
        self._latencies: Deque[float] = deque(maxlen=int(latency_window))
        #: set by the service each poll - the queue is part of the picture
        self.queue_state: dict = {}
        self.rate_state: dict = {}
        #: instrument mapping report, when a provider supplies one
        self.mapping = None
        #: universe selection report, when the watchlist came from a spec
        self.universe_report = None

        self.cache_hits = 0
        self.cache_misses = 0

    # ------------------------------------------------------------ watchlist

    def set_symbols(self, symbols) -> None:
        self.symbols = list(symbols)
        for sym in self.symbols:
            self.symbol_health.setdefault(sym, SymbolHealth(symbol=sym))

    def health_for(self, symbol: str) -> SymbolHealth:
        health = self.symbol_health.get(symbol)
        if health is None:
            health = SymbolHealth(symbol=symbol)
            self.symbol_health[symbol] = health
        return health

    def tf_health(self, timeframe: str) -> TimeframeHealth:
        health = self.timeframe_health.get(timeframe)
        if health is None:
            health = TimeframeHealth(timeframe=timeframe)
            self.timeframe_health[timeframe] = health
        return health

    # -------------------------------------------------------------- candles

    def _stat(self, symbol: str, timeframe: str):
        path = self.store._path(symbol, timeframe)
        try:
            st = path.stat()
            return st.st_mtime_ns, st.st_size
        except OSError:
            return None

    def history(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """The warmed-up window handed to strategies.

        Served from cache when the underlying file has not changed. The
        validation is deliberate rather than trusting: the downloader, a test
        or a second process may write the same store, and a cache that assumed
        exclusive ownership would quietly serve superseded candles - the exact
        class of defect (acting on data you believe is current) this subsystem
        exists to eliminate.
        """
        key = (symbol, timeframe)
        stat = self._stat(symbol, timeframe)
        cached = self._cache.get(key)
        if cached is not None:
            if stat is not None and (cached.mtime_ns, cached.size) == stat:
                self._cache.move_to_end(key)
                self.cache_hits += 1
                return cached.frame
            if stat is None:
                # file vanished under us; drop the cache rather than serve it
                self._cache.pop(key, None)
        self.cache_misses += 1
        frame = self.store.read(symbol, timeframe)
        if not ohlcv.is_empty(frame):
            frame = frame.tail(self.history_bars).reset_index(drop=True)
        if stat is not None:
            self._cache[key] = _Cached(frame, stat[0], stat[1])
            self._cache.move_to_end(key)
            self._evict()
        return frame

    def _evict(self) -> None:
        while len(self._cache) > self.max_cached_series:
            self._cache.popitem(last=False)

    def last_bar(self, symbol: str, timeframe: str) -> Optional[pd.Timestamp]:
        frame = self.history(symbol, timeframe)
        if ohlcv.is_empty(frame):
            return None
        return pd.Timestamp(frame["date"].iloc[-1])

    #: the name the trading stages used when this surface lived on the feed
    latest_bar_time = last_bar

    def marks(self, timeframe: str) -> Dict[str, tuple]:
        """``symbol -> (close, bar_open_time)``.

        The price and the bar it belongs to travel TOGETHER so they cannot be
        separated by accident; a price without its timestamp is how hours-old
        candles were once presented as live prices.
        """
        out: Dict[str, tuple] = {}
        for sym in self.symbols:
            frame = self.history(sym, timeframe)
            if not ohlcv.is_empty(frame):
                out[sym] = (float(frame["close"].iloc[-1]),
                            pd.Timestamp(frame["date"].iloc[-1]))
        return out

    def latest_prices(self, timeframe: str) -> Dict[str, float]:
        return {s: m[0] for s, m in self.marks(timeframe).items()}

    def apply_candles(self, symbol: str, timeframe: str,
                      frame: pd.DataFrame) -> int:
        """Persist newly fetched bars and refresh the cache. Returns rows added.

        Write-through: the store is updated first (durability), then the cache
        is invalidated by the store's own new mtime, so the next read reloads.
        There is no path by which the cache can hold bars the store does not.
        """
        if ohlcv.is_empty(frame):
            return 0
        added = self.store.write(symbol, timeframe, frame)
        self._cache.pop((symbol, timeframe), None)
        if added:
            now = pd.Timestamp.now(tz="UTC")
            self.last_update[timeframe] = now
            self.tf_health(timeframe).rows_added += added
            self.health_for(symbol).rows_added += added
        return added

    # --------------------------------------------------------------- health

    def record_success(self, symbol: str, timeframe: str,
                       rows: int = 0, latency_s: Optional[float] = None
                       ) -> None:
        health = self.health_for(symbol)
        health.consecutive_failures = 0
        health.total_successes += 1
        health.status = H_OK
        health.last_success = pd.Timestamp.now(tz="UTC")
        health.last_attempt = health.last_success
        tf = self.tf_health(timeframe)
        tf.served += 1
        tf.last_attempt = health.last_attempt
        if rows:
            tf.last_success = health.last_success
        if latency_s is not None:
            self._latencies.append(float(latency_s))

    def record_failure(self, symbol: str, timeframe: str, error: str,
                       unavailable: bool = False,
                       latency_s: Optional[float] = None) -> None:
        """One symbol failed. This is a symbol-level event by construction -
        there is no code path from here to halting the timeframe."""
        health = self.health_for(symbol)
        health.consecutive_failures += 1
        health.total_failures += 1
        health.last_error = str(error)
        health.last_attempt = pd.Timestamp.now(tz="UTC")
        if unavailable:
            health.status = H_UNAVAILABLE
        elif health.consecutive_failures >= self.failure_threshold:
            health.status = H_FAILED
        else:
            health.status = H_DEGRADED
        tf = self.tf_health(timeframe)
        tf.failed += 1
        tf.last_attempt = health.last_attempt
        if latency_s is not None:
            self._latencies.append(float(latency_s))

    def usable_symbols(self, timeframe: Optional[str] = None) -> List[str]:
        """Symbols worth scanning: everything not set aside by health.

        This is principle 10 in one line - 99 configured, 3 unreachable, 96
        scanned. Nothing here can return an empty list because of a provider
        fault; a symbol with stored bars stays usable even while its fetches
        fail, because the bars it already has are still real.
        """
        return [s for s in self.symbols if self.health_for(s).usable]

    def skipped_symbols(self) -> List[str]:
        return [s for s in self.symbols if not self.health_for(s).usable]

    # ------------------------------------------------------------ freshness

    def freshness(self, timeframe: str, clock=None, now=None):
        """How far each symbol lags the bar that is actually due.

        Returns a ``algo.trading.freshness.FreshnessReport``.

        Candle-based and unchanged in substance (D-039): the verdict comes from
        ``algo.trading.freshness``, which compares each symbol's newest stored
        bar to the bar the exchange should have completed. ``clock`` supplies
        the expectation; without one nothing is judged late, because "no bar is
        due" and "the bar is missing" are different facts.
        """
        from algo.trading import freshness as fr
        from algo.trading.clock import IST

        if now is None:
            now = (pd.Timestamp(clock.now()) if clock is not None
                   else pd.Timestamp.now(tz="UTC"))
        now = pd.Timestamp(now)
        now = now.tz_localize("UTC") if now.tzinfo is None \
            else now.tz_convert("UTC")
        expected = None
        market_open = False
        if clock is not None:
            at = now.tz_convert(IST).to_pydatetime()
            market_open = bool(clock.is_open(at))
            bar_open = clock.last_closed_bar_open(timeframe, at=at)
            if bar_open is not None and market_open:
                expected = pd.Timestamp(bar_open).tz_convert("UTC")
        last_bars = {s: m[1] for s, m in self.marks(timeframe).items()}
        return fr.build_report(self.symbols, last_bars, timeframe, expected,
                               market_open, now=now,
                               tolerance_bars=self.stale_tolerance_bars)

    # ------------------------------------------------------------- provider

    def note_provider(self, name: str, status: str, detail: str = "") -> None:
        self.provider_name = name
        self.provider_status = status
        self.provider_detail = detail

    def mapping_report(self):
        """The watchlist resolved through THE authoritative instrument map.

        None when no provider is wired (offline paper), where symbol->token
        mapping does not apply. Nothing here keeps its own lookup - the answer
        comes from the source's instrument master, the same object the data
        provider and the broker adapter resolve through, so the dashboard
        cannot disagree with the trading engine about what is addressable.
        """
        if self.mapping is None or not hasattr(self.mapping, "resolve_many"):
            return None
        return self.mapping.resolve_many(self.symbols)

    def diagnosis(self, timeframe: str) -> dict:
        """ONE operator-readable verdict for this timeframe's last pass.

        ``rows_added == 0`` has two completely different meanings and the
        operator must never have to work out which from the logs: either every
        symbol is already current (fine), or nothing could be fetched (broken).
        This states which, names the reason, and lists examples - once.

        Same contract the ingestion engine's ``diagnosis`` published, so the
        console and the dashboard tell the identical story; it is derived from
        MarketState instead of from a report object that only the fetch path
        held, which is what let the two disagree.
        """
        tf = self.tf_health(timeframe)
        blocked = [s for s in self.symbols if not self.health_for(s).usable]
        total = len(self.symbols)
        offline = self.provider_status == OFFLINE
        reasons: Dict[str, int] = {}
        for sym in blocked:
            reason = self.health_for(sym).last_error or "unavailable"
            reasons[reason] = reasons.get(reason, 0) + 1
        if offline:
            # Offline is a configured MODE, not a fault: no provider was wired,
            # so there is nothing that could have been fetched. Reporting it as
            # "UNABLE TO FETCH" would raise an alarm about a situation the
            # operator chose and was told about at startup.
            return {"ok": True, "offline": True, "timeframe": timeframe,
                    "blocked": 0, "total": total, "reasons": {},
                    "examples": [],
                    "headline": f"{timeframe}: offline ({self.provider_detail}"
                                f") - serving stored candles only"}
        if not blocked:
            headline = (f"{timeframe}: updated {tf.served}/{total} symbols"
                        if tf.rows_added else
                        f"{timeframe}: no new candles ({total} symbols current)")
            return {"ok": True, "offline": offline, "timeframe": timeframe,
                    "blocked": 0, "total": total, "reasons": {},
                    "examples": [], "headline": headline}
        top = max(reasons.items(), key=lambda kv: kv[1])[0] if reasons else ""
        shown = ", ".join(blocked[:5])
        more = f", +{len(blocked) - 5} more" if len(blocked) > 5 else ""
        return {
            "ok": False, "offline": offline, "timeframe": timeframe,
            "blocked": len(blocked), "total": total, "reasons": reasons,
            "examples": blocked[:5],
            "headline": (f"{timeframe}: UNABLE TO FETCH candles for "
                         f"{len(blocked)}/{total} symbols - {top} "
                         f"(examples: {shown}{more})"),
        }

    @property
    def latency_ms(self) -> Optional[float]:
        if not self._latencies:
            return None
        return round(1000.0 * sum(self._latencies) / len(self._latencies), 1)

    def latency_percentile(self, pct: float) -> Optional[float]:
        if not self._latencies:
            return None
        ordered = sorted(self._latencies)
        idx = min(len(ordered) - 1,
                  max(0, int(round(pct / 100.0 * (len(ordered) - 1)))))
        return round(1000.0 * ordered[idx], 1)

    def data_ok(self, timeframe: str) -> bool:
        """Did the last pass on this timeframe actually serve the watchlist?

        Reports the FETCH, not the store: a store that stopped updating still
        returns prices, and calling that fresh is how a whole session traded on
        stale bars while every health beat said ok (D-038).
        """
        health = self.timeframe_health.get(timeframe)
        return True if health is None else health.ok

    # ------------------------------------------------------------ reporting

    def summary(self, timeframe: str) -> dict:
        """One dict describing this timeframe - the dashboard's whole input."""
        tf = self.tf_health(timeframe)
        skipped = self.skipped_symbols()
        unavailable = [s for s in skipped
                       if self.health_for(s).status == H_UNAVAILABLE]
        return {
            "timeframe": timeframe,
            "provider": self.provider_name,
            "provider_status": self.provider_status,
            "provider_detail": self.provider_detail,
            "configured_symbols": len(self.symbols),
            "usable_symbols": len(self.usable_symbols(timeframe)),
            "skipped_symbols": len(skipped),
            "unavailable_symbols": len(unavailable),
            "skipped_examples": skipped[:10],
            "requested": tf.requested,
            "served": tf.served,
            "failed": tf.failed,
            "rows_added": tf.rows_added,
            "pending": tf.pending,
            "ok": tf.ok,
            "headline": tf.headline,
            "last_update": (None if self.last_update.get(timeframe) is None
                            else self.last_update[timeframe].isoformat()),
            "latency_ms": self.latency_ms,
            "latency_p95_ms": self.latency_percentile(95),
            "queue": dict(self.queue_state),
            "rate": dict(self.rate_state),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
        }
