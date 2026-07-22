"""LocalCandleEngine - THE candle constructor for streaming market data.

Builds 1m/3m/5m/15m OHLCV candles from QUOTE ticks, exactly as validated by
the standalone experiments (scripts/websocket_validation.py and
scripts/candle_parity_validation.py: 100% symbol and minute coverage, and
OHLC/volume parity with the Historical Candle API). This module is the
PRODUCTION home of that logic - the diagnostics remain standalone evidence
tools, and no other component may assemble candles.

Design rules (all load-bearing):

* **Exchange timestamps only.** A tick's bucket comes from the exchange
  timestamp it carries; local clocks never place a trade in a bar. Bucket
  assignment is integer arithmetic on epoch seconds - deterministic, no
  timezone dependence (NSE session opens 09:15 IST which is aligned to every
  supported timeframe, so plain flooring is exact for 1/3/5/15 minutes).
* **Cumulative volume.** SmartAPI QUOTE carries the day's cumulative volume;
  a candle's volume is the difference between the cumulative volume at its
  close and at the previous candle's close. A candle whose opening baseline is
  unknown (first bucket after subscribe or after a feed gap) is TAINTED and
  never served as final - the historical API repairs it instead.
* **Immutable completions.** A candle finalizes exactly once - when a tick
  arrives in a later bucket, or when the clock passes the bucket end plus a
  grace period. Late ticks for a finalized bucket are counted and dropped,
  never applied.
* **Bounded memory.** Only the most recent ``max_bars`` finalized candles per
  (symbol, timeframe) are retained; the store owns history.
* **Thread-safe, minimal work under the lock.** ``on_tick`` is integer/float
  updates on plain lists; frames are built on demand by readers.
"""

from __future__ import annotations

import threading
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

from algo.core.logging import get_logger
from algo.data.ohlcv import OHLCV_COLUMNS, timeframe_minutes

logger = get_logger("marketdata.localcandles")

#: Timeframes the engine may build. 09:15 IST (03:45 UTC) is minute 585 of the
#: day: 585 % 1 == 585 % 3 == 585 % 5 == 585 % 15 == 0, so epoch flooring
#: lands exactly on exchange bucket labels for those four. "1h" is DIFFERENT:
#: NSE hourly bars are session-anchored (09:15, 10:15, ..., 15:15), so its
#: buckets are computed from the session-open anchor, and the final partial
#: bucket (15:15) closes at the 15:30 session close, not at 16:15.
SUPPORTED_TIMEFRAMES = ("1m", "3m", "5m", "15m", "1h")

#: NSE session anchor/close as seconds into the UTC day (09:15 and 15:30 IST).
_NSE_OPEN_S = 13_500      # 03:45:00 UTC
_NSE_CLOSE_S = 36_000     # 10:00:00 UTC

# candle list slots (a list, not a dataclass: the hot path updates in place)
_O, _H, _L, _C, _VBASE, _VCUM, _TICKS = range(7)


class LocalCandleEngine:
    """Builds candles from ticks. One instance per feed; the only builder."""

    def __init__(self, timeframes: Iterable[str] = SUPPORTED_TIMEFRAMES,
                 max_bars: int = 512,
                 finalize_grace_s: float = 3.0) -> None:
        unsupported = [tf for tf in timeframes
                       if tf not in SUPPORTED_TIMEFRAMES]
        if unsupported:
            raise ValueError(
                f"LocalCandleEngine cannot build {unsupported}: NSE session "
                f"alignment is only exact for {SUPPORTED_TIMEFRAMES}")
        self.timeframes: Tuple[str, ...] = tuple(dict.fromkeys(timeframes))
        self._tf_seconds = {tf: timeframe_minutes(tf) * 60
                            for tf in self.timeframes}
        self.max_bars = int(max_bars)
        self.finalize_grace_s = float(finalize_grace_s)
        self._lock = threading.Lock()
        #: (symbol, tf) -> {bucket_epoch_s: [o, h, l, c, vbase, vcum, ticks]}
        self._candles: Dict[tuple, Dict[int, list]] = {}
        #: (symbol, tf) -> set of finalized bucket epochs still retained
        self._final: Dict[tuple, set] = {}
        #: (symbol, tf) -> buckets with an unknown volume baseline or a
        #: possible mid-bucket join; never served as final
        self._tainted: Dict[tuple, set] = {}
        #: symbol -> cumulative day volume at the last applied tick
        self._cum_volume: Dict[str, int] = {}
        #: symbols that have delivered at least one tick since construction
        self._seen: set = set()
        #: symbols whose NEXT tick must resync (set by :meth:`mark_gap`; a
        #: never-seen symbol resyncs implicitly on its first tick)
        self._resync: Dict[str, bool] = {}
        #: epoch second of the first CLEAN tick per symbol (serves as the
        #: coverage boundary: windows before it belong to the historical API)
        self.coverage_start: Dict[str, float] = {}
        # counters (observability; read without the lock, approximate is fine)
        self.ticks = 0
        self.late_ticks = 0
        self.completions = 0

    # ---------------------------------------------------------------- intake

    def on_tick(self, symbol: str, ts_epoch_s: float, price: float,
                cum_volume: Optional[int]) -> None:
        """Apply one normalized tick. Cheap, thread-safe, allocation-light."""
        if price is None or price <= 0 or not ts_epoch_s:
            return
        with self._lock:
            self.ticks += 1
            resync = (symbol not in self._seen
                      or self._resync.pop(symbol, False))
            self._seen.add(symbol)
            prev_cum = None if resync else self._cum_volume.get(symbol)
            if cum_volume is not None:
                self._cum_volume[symbol] = cum_volume
            if resync:
                self.coverage_start.setdefault(symbol, ts_epoch_s)
            for tf in self.timeframes:
                self._apply(symbol, tf, ts_epoch_s, price, cum_volume,
                            prev_cum, resync)

    def _bucket(self, tf: str, ts: float) -> int:
        """Deterministic bucket open for a tick: epoch flooring for intraday
        minutes, session-anchored steps for 1h (09:15 + n hours)."""
        step = self._tf_seconds[tf]
        if step < 3600:
            return int(ts // step) * step
        day = int(ts // 86400) * 86400
        delta = max(0.0, ts - day - _NSE_OPEN_S)
        return day + _NSE_OPEN_S + int(delta // step) * step

    def _bucket_end(self, tf: str, bucket: int) -> int:
        """Bucket close: open + step, except the anchored 1h bucket that
        spans the session close (15:15 closes at 15:30, not 16:15)."""
        step = self._tf_seconds[tf]
        if step < 3600:
            return bucket + step
        day = int(bucket // 86400) * 86400
        return min(bucket + step, day + _NSE_CLOSE_S)

    def _apply(self, symbol: str, tf: str, ts: float, price: float,
               cum_volume, prev_cum, resync: bool) -> None:
        bucket = self._bucket(tf, ts)
        key = (symbol, tf)
        candles = self._candles.setdefault(key, {})
        final = self._final.setdefault(key, set())
        if bucket in final:
            self.late_ticks += 1          # immutable: never reopened
            return
        candle = candles.get(bucket)
        if candle is None:
            # a new bucket begins: everything older is now complete
            self._finalize_older(key, bucket)
            candles[bucket] = [price, price, price, price,
                               prev_cum, cum_volume, 1]
            if resync or prev_cum is None:
                # unknown baseline or possible mid-bucket join: repairable
                # only by the historical API, never served locally
                self._tainted.setdefault(key, set()).add(bucket)
            self._prune(key)
            return
        if price > candle[_H]:
            candle[_H] = price
        if price < candle[_L]:
            candle[_L] = price
        candle[_C] = price
        if cum_volume is not None:
            candle[_VCUM] = cum_volume
        candle[_TICKS] += 1

    def _finalize_older(self, key: tuple, before_bucket: int) -> None:
        final = self._final[key]
        for bucket in self._candles[key]:
            if bucket < before_bucket and bucket not in final:
                final.add(bucket)
                self.completions += 1

    def _prune(self, key: tuple) -> None:
        candles = self._candles[key]
        if len(candles) <= self.max_bars:
            return
        final = self._final[key]
        tainted = self._tainted.get(key, set())
        for bucket in sorted(candles)[:len(candles) - self.max_bars]:
            if bucket in final:           # never drop an in-progress candle
                candles.pop(bucket, None)
                final.discard(bucket)
                tainted.discard(bucket)

    # ------------------------------------------------------------ lifecycle

    def mark_gap(self) -> None:
        """The feed disconnected (or is resubscribing): every symbol's next
        tick starts a resync. In-progress buckets are tainted - they may be
        missing trades - and volume baselines are invalidated."""
        with self._lock:
            for key, candles in self._candles.items():
                final = self._final.get(key, set())
                open_buckets = [b for b in candles if b not in final]
                if open_buckets:
                    self._tainted.setdefault(key, set()).update(open_buckets)
            for symbol in self._seen:
                self._resync[symbol] = True
            logger.info("candle engine: gap marked - next ticks resync")

    def finalize_before(self, now_epoch_s: float) -> None:
        """Time-based completion: a bucket whose end passed more than
        ``finalize_grace_s`` ago is complete even if no later tick arrived
        (end of session, or a symbol that went quiet)."""
        with self._lock:
            for (symbol, tf), candles in self._candles.items():
                final = self._final[(symbol, tf)]
                for bucket in candles:
                    if (bucket not in final
                            and self._bucket_end(tf, bucket)
                            + self.finalize_grace_s <= now_epoch_s):
                        final.add(bucket)
                        self.completions += 1

    # -------------------------------------------------------------- serving

    def frame(self, symbol: str, timeframe: str, start_epoch_s: float,
              end_epoch_s: float) -> pd.DataFrame:
        """Finalized, untainted candles in ``[start, end]`` as a canonical
        UTC OHLCV frame. Buckets with no trades simply do not appear."""
        key = (symbol, timeframe)
        rows: List[list] = []
        with self._lock:
            candles = self._candles.get(key, {})
            final = self._final.get(key, set())
            tainted = self._tainted.get(key, set())
            for bucket in sorted(candles):
                if (bucket < start_epoch_s or bucket > end_epoch_s
                        or bucket not in final or bucket in tainted):
                    continue
                c = candles[bucket]
                volume = (float(c[_VCUM] - c[_VBASE])
                          if c[_VCUM] is not None and c[_VBASE] is not None
                          else 0.0)
                rows.append([bucket, c[_O], c[_H], c[_L], c[_C],
                             max(volume, 0.0)])
        if not rows:
            return pd.DataFrame(columns=list(OHLCV_COLUMNS))
        frame = pd.DataFrame(rows, columns=list(OHLCV_COLUMNS))
        frame["date"] = pd.to_datetime(frame["date"], unit="s", utc=True)
        return frame

    def uncovered_before(self, symbol: str, timeframe: str,
                         start_epoch_s: float, end_epoch_s: float,
                         outages: Iterable[Tuple[float, float]] = ()
                         ) -> Optional[Tuple[float, float]]:
        """The sub-window of ``[start, end]`` this engine CANNOT vouch for,
        or None when local candles fully cover the request.

        Uncovered means: before the symbol's first clean tick, overlapping a
        recorded feed outage, or holding a tainted bucket. A bucket that is
        simply empty on a clean, connected feed is a quiet bar, NOT a gap -
        treating it as one would re-poll the historical API for every thin
        minute, which is the polling this engine exists to remove.
        """
        key = (symbol, timeframe)
        step = self._tf_seconds[timeframe]
        with self._lock:
            coverage = self.coverage_start.get(symbol)
            tainted = set(self._tainted.get(key, ()))
        bad_until = None
        # everything before the first clean tick's bucket is historical's job
        if coverage is None:
            return (start_epoch_s, end_epoch_s)
        coverage_bucket = self._bucket(timeframe, coverage)
        first_clean = coverage_bucket + step   # coverage bucket is tainted
        if start_epoch_s < first_clean:
            bad_until = min(end_epoch_s, first_clean - 1)
        for t0, t1 in outages:
            if t1 >= start_epoch_s and t0 <= end_epoch_s:
                # taint every bucket the outage touches
                span_end = min(end_epoch_s, self._bucket_end(
                    timeframe, self._bucket(timeframe, t1)) - 1)
                bad_until = span_end if bad_until is None \
                    else max(bad_until, span_end)
        for bucket in tainted:
            if start_epoch_s <= bucket <= end_epoch_s:
                span_end = min(end_epoch_s,
                               self._bucket_end(timeframe, bucket) - 1)
                bad_until = span_end if bad_until is None \
                    else max(bad_until, span_end)
        if bad_until is None:
            return None
        return (start_epoch_s, bad_until)

    # ---------------------------------------------------------- observation

    def snapshot(self) -> dict:
        with self._lock:
            per_tf = {tf: 0 for tf in self.timeframes}
            tainted = 0
            for (symbol, tf), final in self._final.items():
                per_tf[tf] = per_tf.get(tf, 0) + len(final)
            for buckets in self._tainted.values():
                tainted += len(buckets)
            return {
                "ticks": self.ticks,
                "late_ticks": self.late_ticks,
                "completions": self.completions,
                "finalized_by_timeframe": per_tf,
                "tainted_buckets": tainted,
                "symbols_covered": len(self.coverage_start),
            }
