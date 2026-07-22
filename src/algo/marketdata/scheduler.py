"""TimeframeScheduler - owns every timeframe, decides WHAT to fetch and when.

This is the object that replaces ``for symbol: fetch(); store()``. It never
sends anything; it produces :class:`DataRequest` objects and hands them to a
queue. That is the whole separation of scheduling from transport - the two
never appear in the same function, and no scheduling decision lives inside
provider code.

Three invariants, each of which was a real defect class before:

**Nothing is fetched early.** A timeframe becomes due only when the clock says
a new bar has COMPLETED and the grace window (feed latency) has elapsed. Asking
for a bar that is still forming returns a partial candle, and a partial candle
that lands in the store is indistinguishable from a completed one afterwards.

**Nothing is skipped once due.** A due timeframe stays pending until every due
symbol has been attempted. A poll that runs out of rate budget defers work; it
does not drop it. If the engine was down for three bars, the incremental window
covers all three in one request - bars are never skipped, only coalesced.

**Only due symbols are asked for.** A symbol whose newest stored bar already
IS the expected bar needs nothing, and is not requested. That is what makes
this scale: after the first pass of a session, a symbol costs one request per
bar and zero when its bar has already arrived. Completed data is never
re-requested.

The scheduler reads MarketState to find out what is stored. It does not read
the store, does not know what a parquet file is, and does not know whether the
answer will arrive by REST or by stream.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Set

import pandas as pd

from algo.core.logging import get_logger
from algo.data import ohlcv
from algo.marketdata.capabilities import ProviderCapabilities
from algo.marketdata.queue import (
    CANDLES, DataRequest, P_BACKFILL, P_DUE, P_HELD, P_QUOTE, QUOTES,
)

logger = get_logger("marketdata.scheduler")


#: THE timeframe->minutes definition, shared with the data layer rather than
#: re-parsed here. The ad-hoc parser this replaces read the "d" of "1d" as a
#: minute unit, which made a daily timeframe report as due every minute.
_tf_minutes = ohlcv.timeframe_minutes


class TimeframeScheduler:
    """Per-timeframe due tracking and request planning."""

    def __init__(self, clock, timeframes: Sequence[str],
                 grace_seconds: int = 20,
                 capabilities: Optional[ProviderCapabilities] = None,
                 live_lookback_days: int = 5,
                 max_attempts: int = 2) -> None:
        self.clock = clock
        self.timeframes = list(timeframes)
        self.grace = timedelta(seconds=int(grace_seconds))
        self.capabilities = capabilities
        #: seed window for a symbol with no stored bars on the LIVE path. The
        #: ingestion default is a year, which is right for a download and badly
        #: wrong here - one uncovered timeframe would pull a year per symbol at
        #: the first refresh.
        self.live_lookback_days = int(live_lookback_days)
        #: attempts per symbol per bar before it is left to the next bar. A
        #: symbol that fails twice is a symbol problem, not a transport blip;
        #: retrying it forever would spend the whole budget on the broken few.
        self.max_attempts = int(max_attempts)

        #: timeframe -> the completed bar we have finished serving
        self._served_bar: Dict[str, pd.Timestamp] = {}
        #: timeframe -> the completed bar currently being served
        self._serving_bar: Dict[str, pd.Timestamp] = {}
        #: timeframe -> symbols still owed a bar in the current pass
        self._pending: Dict[str, Set[str]] = {}
        #: (timeframe, symbol) -> attempts made for the bar being served
        self._attempts: Dict[tuple, int] = {}
        #: Per-pass counters used by the engine's lifecycle reporting. These
        #: do not participate in planning; they describe the current obligation
        #: independently of cumulative timeframe health counters.
        self._pass_total: Dict[str, int] = {}
        self._pass_served: Dict[str, int] = {}
        self._pass_failed: Dict[str, int] = {}

    # ------------------------------------------------------------- bar time

    def expected_bar(self, timeframe: str,
                     at: Optional[datetime] = None) -> Optional[pd.Timestamp]:
        """Open time (UTC) of the newest bar the exchange should have
        completed, or None when none is due (market shut, or pre-first-bar)."""
        at = at or self.clock.now()
        if not self.clock.is_open(at) and not self.clock.past_squareoff(at):
            return None
        bar = self.clock.last_closed_bar_open(timeframe, at=at)
        if bar is None:
            return None
        return pd.Timestamp(bar).tz_convert("UTC")

    def next_due_at(self, timeframe: str,
                    at: Optional[datetime] = None) -> Optional[datetime]:
        """Wall-clock (IST) moment this timeframe next becomes due.

        This is the ``next_due`` column an operator reads: 5m -> 09:20, 09:25;
        15m -> 09:30, 09:45; 1h -> 10:00, 11:00. It is the next bar CLOSE plus
        the grace window, because that is when the bar is both complete and
        fetchable.
        """
        at = at or self.clock.now()
        try:
            return self.clock.next_bar_close(timeframe, at=at) + self.grace
        except Exception:
            return None

    def seconds_until_next(self, at: Optional[datetime] = None) -> float:
        """Sleep hint: seconds to the soonest due moment across timeframes.

        Capped so square-off and kill-switch checks stay responsive, and
        floored so a caller cannot spin.
        """
        at = at or self.clock.now()
        nexts = [d for d in (self.next_due_at(tf, at)
                             for tf in self.timeframes) if d is not None]
        if not nexts:
            return 30.0
        return max(1.0, min(60.0, (min(nexts) - at).total_seconds()))

    # ---------------------------------------------------------------- due

    def is_due(self, timeframe: str, at: Optional[datetime] = None) -> bool:
        """Has a completed bar arrived that we have not finished serving?"""
        at = at or self.clock.now()
        expected = self.expected_bar(timeframe, at)
        if expected is None:
            return False
        # the bar must be complete AND the grace window elapsed, so the feed
        # has had time to publish it
        bar_close = (pd.Timestamp(expected).tz_convert("UTC")
                     + pd.Timedelta(minutes=_tf_minutes(timeframe)))
        now_utc = pd.Timestamp(at).tz_convert("UTC")
        if now_utc < bar_close + pd.Timedelta(seconds=self.grace.total_seconds()):
            return False
        if self._pending.get(timeframe):
            return True                       # work outstanding for this bar
        return self._served_bar.get(timeframe) != expected

    def due_timeframes(self, at: Optional[datetime] = None) -> List[str]:
        """Every timeframe with outstanding work, soonest bar first.

        Ordered by bar length so a 15m bar is planned before a 1h one when both
        land together: the shorter timeframe has less time before its next bar,
        so it is the one that can actually miss its window.
        """
        at = at or self.clock.now()
        due = [tf for tf in self.timeframes if self.is_due(tf, at)]
        return sorted(due, key=_tf_minutes)

    def symbols_due(self, timeframe: str, state,
                    at: Optional[datetime] = None) -> List[str]:
        """Symbols that need a new completed bar on this timeframe.

        A symbol whose newest stored bar is already the expected bar is NOT
        due: its data is complete and re-requesting it would spend budget to
        receive bars we hold. This is the difference between a fetch that costs
        one request per symbol per bar and one that costs a request per symbol
        per cycle.
        """
        at = at or self.clock.now()
        expected = self.expected_bar(timeframe, at)
        if expected is None:
            return []
        out: List[str] = []
        for symbol in state.usable_symbols(timeframe):
            last = state.last_bar(symbol, timeframe)
            if last is None or pd.Timestamp(last) < expected:
                out.append(symbol)
        return out

    # ----------------------------------------------------------- planning

    def plan(self, state, at: Optional[datetime] = None,
             held: Sequence[str] = ()) -> List[DataRequest]:
        """The requests that should exist right now. Pure: sends nothing.

        Called every poll. Requests already queued are de-duplicated by the
        queue, so calling this repeatedly inside one bar is free and idempotent.
        """
        at = at or self.clock.now()
        held_set = set(held)
        requests: List[DataRequest] = []
        for timeframe in self.due_timeframes(at):
            expected = self.expected_bar(timeframe, at)
            if expected is None:
                continue
            self._begin(timeframe, expected, state, at)
            for symbol in sorted(self._pending.get(timeframe, ())):
                if self._attempts.get((timeframe, symbol), 0) \
                        >= self.max_attempts:
                    continue
                request = self._candle_request(state, symbol, timeframe,
                                               expected, held_set)
                if request is not None:
                    requests.append(request)
        return requests

    def _begin(self, timeframe: str, expected: pd.Timestamp, state,
               at: datetime) -> None:
        """Open a serving pass for ``expected``, or continue the current one."""
        if self._serving_bar.get(timeframe) == expected:
            return                                    # already in progress
        if self._source_offline():
            # No provider is wired at all. That is a configured MODE, not a
            # fault: there is nothing to request, and the candles already in
            # the store are still real and still tradeable. Close the pass
            # immediately so scans keep running on stored data, and do NOT
            # mark symbols unavailable - doing so would empty the watchlist
            # and silently turn offline paper trading into no trading.
            self._serving_bar[timeframe] = expected
            self._served_bar[timeframe] = expected
            self._pending[timeframe] = set()
            self._pass_total[timeframe] = 0
            self._pass_served[timeframe] = 0
            self._pass_failed[timeframe] = 0
            state.tf_health(timeframe).pending = 0
            return
        # a new bar supersedes an unfinished pass: its window covers whatever
        # the previous pass did not fetch, so nothing is lost by moving on
        self._serving_bar[timeframe] = expected
        due = self.symbols_due(timeframe, state, at)
        self._pending[timeframe] = set(due)
        self._pass_total[timeframe] = len(due)
        self._pass_served[timeframe] = 0
        self._pass_failed[timeframe] = 0
        for key in [k for k in self._attempts if k[0] == timeframe]:
            self._attempts.pop(key, None)
        health = state.tf_health(timeframe)
        health.requested += len(due)
        health.pending = len(due)
        if due:
            logger.info("%s: bar %s due for %d/%d symbols", timeframe,
                        expected, len(due), len(state.symbols))
        else:
            # nothing to do for this bar - close it immediately so the
            # timeframe does not read as perpetually pending
            self._served_bar[timeframe] = expected

    def _candle_request(self, state, symbol: str, timeframe: str,
                        expected: pd.Timestamp,
                        held: Set[str]) -> Optional[DataRequest]:
        """Build the INCREMENTAL window for one symbol.

        The window starts one bar after what is stored - not at a fixed
        lookback - so the REST request itself carries only the missing bars.
        Verifying that is the point of ``test_rest_window_is_incremental``:
        writing only new rows to the store while downloading a year every time
        would look identical from the store's side.
        """
        reason = None
        source = getattr(self, "_source", None)
        if source is not None:
            reason = source.unavailable_reason(symbol, timeframe)
        if reason:
            state.record_failure(symbol, timeframe, reason, unavailable=True)
            self._resolve(timeframe, symbol, state, outcome="failed")
            return None

        step = pd.Timedelta(minutes=_tf_minutes(timeframe))
        last = state.last_bar(symbol, timeframe)
        end = expected + step - pd.Timedelta(minutes=1)
        if last is None:
            start = (pd.Timestamp.now(tz="UTC")
                     - pd.Timedelta(days=self.live_lookback_days))
            priority = P_BACKFILL          # seeding must not delay live bars
            why = f"seed {self.live_lookback_days}d"
        else:
            start = pd.Timestamp(last) + step
            priority = P_HELD if symbol in held else P_DUE
            why = f"incremental from {start}"
        if start > end:
            # already current: nothing to ask for
            self._resolve(timeframe, symbol, state, outcome="served")
            return None
        # NB: attempts are counted in mark_served (once per real execution), not
        # here. plan() runs every poll and the queue de-duplicates, so counting
        # per plan call would let a symbol that sits queued-but-unsent under a
        # tight rate budget burn its whole attempt budget without ever being
        # fetched, then be discharged as failed. The count must track fetches.
        return DataRequest(
            kind=CANDLES, timeframe=timeframe, symbols=(symbol,),
            start=start, end=end, priority=priority, due_bar=expected,
            reason=why,
            attempts=self._attempts.get((timeframe, symbol), 0))

    def plan_quotes(self, symbols: Sequence[str],
                    capabilities: Optional[ProviderCapabilities] = None
                    ) -> List[DataRequest]:
        """Batched quote requests for display marking.

        Split to exactly what the provider serves in one call. On SmartAPI that
        is 50 symbols per request, so a 1000-symbol universe is 20 requests
        rather than 1000 - which is why the whole watchlist can be marked while
        candles stay on their own budget.
        """
        caps = capabilities or self.capabilities
        if caps is None or not caps.quotes_batchable():
            batches = [[s] for s in symbols]
        else:
            batches = caps.quote_batches(symbols)
        return [DataRequest(kind=QUOTES, timeframe="", symbols=tuple(batch),
                            priority=P_QUOTE, reason="mark positions")
                for batch in batches if batch]

    # ---------------------------------------------------------- completion

    def _resolve(self, timeframe: str, symbol: str, state,
                 outcome: str = "") -> None:
        """This symbol's obligation for the current bar is discharged."""
        pending = self._pending.get(timeframe)
        if pending is not None:
            was_pending = symbol in pending
            pending.discard(symbol)
            if was_pending:
                if outcome == "served":
                    self._pass_served[timeframe] = \
                        self._pass_served.get(timeframe, 0) + 1
                elif outcome == "failed":
                    self._pass_failed[timeframe] = \
                        self._pass_failed.get(timeframe, 0) + 1
            state.tf_health(timeframe).pending = len(pending)
            if not pending:
                serving = self._serving_bar.get(timeframe)
                if serving is not None:
                    self._served_bar[timeframe] = serving
                    logger.debug("%s: bar %s fully served", timeframe, serving)

    def mark_served(self, request: DataRequest, state,
                    success: bool = True) -> None:
        """Record the outcome of one request against the pass it belongs to.

        A bar is discharged as SERVED only when the symbol's stored data has
        actually reached the expected bar. A request that succeeded but left
        the symbol still behind - a provider slow to publish the newest bar, or
        an empty window - is NOT done: it is retried within the attempt budget,
        so the bar is caught as soon as it lands rather than being written off
        on data we did not get. Without this, an empty response for a
        not-yet-published bar closed the pass, and the bar was only picked up
        when a LATER bar became due (up to one interval late).

        A FAILURE, or an exhausted retry budget, discharges the obligation too -
        otherwise a permanently unreachable or permanently empty symbol keeps
        its timeframe pending forever and every later bar inherits work that can
        never complete. Health is unaffected: an empty window is not a fault
        (the service records it as a success), only an incomplete SERVE.
        """
        if request.kind != CANDLES:
            return
        timeframe, symbol = request.timeframe, request.symbol
        current = self._serving_bar.get(timeframe)
        if (request.due_bar is not None and current is not None
                and pd.Timestamp(request.due_bar) < pd.Timestamp(current)):
            return
        # one real fetch happened: count it here, so the attempt budget tracks
        # executions, not plan iterations (see _candle_request)
        key = (timeframe, symbol)
        self._attempts[key] = self._attempts.get(key, 0) + 1
        if success and request.due_bar is not None:
            last = state.last_bar(symbol, timeframe)
            if last is None or pd.Timestamp(last) < request.due_bar:
                success = False           # the expected bar has not arrived yet
        if success:
            self._resolve(timeframe, symbol, state, outcome="served")
            return
        if self._attempts.get(key, 0) >= self.max_attempts:
            self._resolve(timeframe, symbol, state, outcome="failed")

    def pending_count(self, timeframe: str) -> int:
        return len(self._pending.get(timeframe, ()))

    def pass_progress(self, timeframe: str) -> dict:
        """Return exact progress for the currently serving bar.

        The timeframe health counters are cumulative across the session. The
        engine needs pass-local numbers so ``1/99 served`` cannot be confused
        with a completed timeframe or with the previous pass.
        """
        serving = self._serving_bar.get(timeframe)
        return {
            "expected_bar": None if serving is None else str(serving),
            "total": self._pass_total.get(timeframe, 0),
            "served": self._pass_served.get(timeframe, 0),
            "failed": self._pass_failed.get(timeframe, 0),
            "pending": self.pending_count(timeframe),
        }

    def _source_offline(self) -> bool:
        """Is there no provider AT ALL? Distinct from one that is failing.

        A source that declares itself offline was never wired; a source whose
        requests are failing is wired and broken. The first is a mode and
        affects every symbol equally; the second is measured per symbol and
        must not be allowed to empty the watchlist.
        """
        source = getattr(self, "_source", None)
        try:
            return source is not None and source.status() == "OFFLINE"
        except Exception:
            return False

    def bind_source(self, source) -> None:
        """Give the scheduler read-only access to the source's addressability
        check, so an unaddressable symbol is never queued at all.

        This is the ONE thing the scheduler asks a source, and it is a
        question, not an instruction: no transport, no I/O, no scheduling
        state crosses in either direction.
        """
        self._source = source

    # ------------------------------------------------------------ reporting

    def snapshot(self, at: Optional[datetime] = None) -> dict:
        """The scheduler table an operator reads: per timeframe, what is due
        and when the next one lands."""
        at = at or self.clock.now()
        rows = []
        for tf in self.timeframes:
            nxt = self.next_due_at(tf, at)
            expected = self.expected_bar(tf, at)
            rows.append({
                "timeframe": tf,
                "expected_bar": None if expected is None else str(expected),
                "served_bar": (None if self._served_bar.get(tf) is None
                                else str(self._served_bar[tf])),
                "due": self.is_due(tf, at),
                "pending": self.pending_count(tf),
                "pass_total": self._pass_total.get(tf, 0),
                "pass_served": self._pass_served.get(tf, 0),
                "pass_failed": self._pass_failed.get(tf, 0),
                "next_due": None if nxt is None else nxt.strftime("%H:%M:%S"),
                "next_due_seconds": (None if nxt is None
                                     else max(0, int((nxt - at).total_seconds()))),
            })
        return {"timeframes": rows,
                "seconds_until_next": self.seconds_until_next(at)}
