"""MarketDataService - THE one owner of market updates.

Everything below the scanner meets here. The service holds the scheduler, the
queue, the transport, the source and the state, and it is the only object that
holds all five; nothing else in the system may fetch, and nothing else may
decide when to.

**One loop, by construction.** There is no thread, no timer and no background
task in this package. :meth:`poll` performs ONE bounded, non-blocking step of
the pipeline and returns - the caller's existing loop drives it. That is not a
stylistic preference: a second loop is a second owner, and the previous
architecture had three (the cycle's ``feed.refresh``, the live tier's own quote
timer, and the bar scheduler), which is why the same symbol could be requested
twice in a second while a due timeframe went unserved.

**Bounded, never blocking.** A 15m bar closing on 99 symbols is 99 requests,
and SmartAPI serves ~3/s: the pass takes ~33 seconds and CANNOT be delivered
inside one call. So ``poll()`` spends what the rate limiter allows right now and
returns; the caller polls again. Square-off, the kill switch and position
management therefore keep running at loop cadence while a fetch pass is in
flight - which a blocking drain would have suspended for half a minute.

**Completion is explicit.** :meth:`completed_timeframes` reports which
timeframes finished their pass since it was last asked, so the scanner runs
once per completed bar - on data that has actually arrived - rather than on a
fixed timer that hopes. When a pass cannot finish (budget exhausted, symbols
unreachable), ``scan_deadline_seconds`` releases it anyway with whatever
arrived; the freshness report then states exactly which symbols are behind,
because a late scan on named partial data is safer than a skipped one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence

import pandas as pd

from algo.core.logging import get_logger
from algo.data import ohlcv
from algo.marketdata.capabilities import ProviderCapabilities
from algo.marketdata.queue import (
    CANDLES, DataRequest, P_QUOTE, QUOTES, RequestQueue,
)
from algo.marketdata.ratelimit import AdaptiveRateLimiter
from algo.marketdata.scheduler import TimeframeScheduler
from algo.marketdata.source import MarketDataSource, NullSource
from algo.marketdata.state import (
    DEGRADED, MarketState, OFFLINE, OK, PENDING,
)
from algo.marketdata.transport import FetchResult, Transport

logger = get_logger("marketdata.service")


@dataclass
class PollReport:
    """What one :meth:`MarketDataService.poll` actually did."""

    sent: int = 0
    served: int = 0
    failed: int = 0
    rows: int = 0
    #: requests left in the queue because the rate budget was spent
    deferred: int = 0
    #: requests re-queued after the provider refused us for rate
    requeued: int = 0
    quotes: int = 0
    elapsed_s: float = 0.0
    due_timeframes: List[str] = field(default_factory=list)
    completed: List[str] = field(default_factory=list)
    queue_depth: int = 0
    #: seconds until the rate limiter will allow the next request (0 = now)
    wait_s: float = 0.0

    @property
    def idle(self) -> bool:
        return self.sent == 0 and not self.due_timeframes

    def to_dict(self) -> dict:
        return {
            "sent": self.sent, "served": self.served, "failed": self.failed,
            "rows": self.rows, "deferred": self.deferred,
            "requeued": self.requeued, "quotes": self.quotes,
            "elapsed_ms": round(self.elapsed_s * 1000, 1),
            "due": list(self.due_timeframes), "completed": list(self.completed),
            "queue_depth": self.queue_depth, "wait_s": round(self.wait_s, 2),
        }


class MarketDataService:
    """Owns the market-data pipeline. The only fetcher in the system."""

    def __init__(self, state: MarketState, scheduler: TimeframeScheduler,
                 transport: Transport, source: MarketDataSource,
                 queue: Optional[RequestQueue] = None,
                 clock=None,
                 max_requests_per_poll: int = 64,
                 poll_budget_seconds: float = 2.0,
                 quote_interval_s: float = 5.0,
                 quotes_enabled: bool = True,
                 scan_deadline_seconds: float = 0.0) -> None:
        self.state = state
        self.scheduler = scheduler
        self.transport = transport
        self.source = source
        self.queue = queue or RequestQueue()
        self.clock = clock
        #: hard ceiling on requests issued in one poll, so a large due set
        #: cannot monopolise the caller's loop even when the budget allows it
        self.max_requests_per_poll = int(max_requests_per_poll)
        #: wall-clock ceiling for one poll. Belt and braces with the count:
        #: a slow provider makes 64 requests take minutes, and the loop still
        #: has square-off to check.
        self.poll_budget_seconds = float(poll_budget_seconds)
        self.quote_interval_s = float(quote_interval_s)
        self.quotes_enabled = bool(quotes_enabled)
        #: release a pending timeframe for scanning after this long even if its
        #: pass has not completed. 0 disables (wait for genuine completion).
        self.scan_deadline_seconds = float(scan_deadline_seconds)

        self.scheduler.bind_source(source)
        self.state.note_provider(source.name, PENDING, "not yet polled")
        # give MarketState the source's instrument master, so the dashboard's
        # token-mapping panel and preflight resolve symbols through the SAME
        # object the provider fetches through - a wired provider must not read
        # as "offline, no mapping" (which it did until this was wired).
        self.state.mapping = source.mapping_report()

        self._last_quote_at: Optional[float] = None
        #: timeframes whose pass finished but whose completion has not been
        #: collected by the caller yet
        self._completed: List[str] = []
        #: timeframe -> monotonic time its current pass opened
        self._pass_opened: Dict[str, float] = {}
        #: consecutive polls in which every attempted request failed
        self._blind_polls = 0
        self.polls = 0

    # ------------------------------------------------------------- assembly

    @classmethod
    def build(cls, store, source: Optional[MarketDataSource], clock,
              symbols: Sequence[str], timeframes: Sequence[str],
              history_bars: int = 1600,
              stale_tolerance_bars: int = 1,
              live_lookback_days: int = 5,
              grace_seconds: int = 20,
              quote_interval_s: float = 5.0,
              quotes_enabled: bool = True,
              scan_deadline_seconds: float = 0.0,
              max_requests_per_poll: int = 64,
              poll_budget_seconds: float = 2.0) -> "MarketDataService":
        """Assemble the whole subsystem around one source.

        ``source=None`` means offline: :class:`NullSource` refuses every fetch
        with a reason, while MarketState keeps serving the candles the store
        already holds. Paper trading without credentials therefore runs the
        real pipeline rather than a special case of it.
        """
        source = source or NullSource()
        state = MarketState(store, symbols=symbols, timeframes=timeframes,
                            history_bars=history_bars,
                            stale_tolerance_bars=stale_tolerance_bars)
        state.set_symbols(symbols)
        scheduler = TimeframeScheduler(
            clock, timeframes, grace_seconds=grace_seconds,
            capabilities=source.capabilities,
            live_lookback_days=live_lookback_days)
        transport = Transport(source)
        service = cls(state, scheduler, transport, source, clock=clock,
                      quote_interval_s=quote_interval_s,
                      quotes_enabled=quotes_enabled,
                      scan_deadline_seconds=scan_deadline_seconds,
                      max_requests_per_poll=max_requests_per_poll,
                      poll_budget_seconds=poll_budget_seconds)
        service.check_budget()
        return service

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self.source.capabilities

    def set_symbols(self, symbols: Sequence[str]) -> None:
        self.state.set_symbols(symbols)

    # ---------------------------------------------------------- feasibility

    def check_budget(self) -> dict:
        """Can this universe be served at this cadence? Answered at STARTUP.

        One candle request per symbol per bar costs ``symbols x bars_per_hour``
        requests an hour. Against SmartAPI's 5,000/hour that is fine for 99
        symbols on 15m (396/h) and impossible for 1,000 on 5m (12,000/h). The
        operator learns which at startup instead of at 12:30 when the hour's
        budget runs out mid-session and every later request is refused.
        """
        caps = self.capabilities
        hourly = caps.hourly_candle_budget()
        n = len(self.state.symbols)
        rows, demand = [], 0
        for tf in self.scheduler.timeframes:
            try:
                minutes = _tf_minutes(tf)
            except Exception:
                continue
            per_hour = (60.0 / minutes) if minutes else 0.0
            need = int(round(n * per_hour))
            demand += need
            rows.append({"timeframe": tf, "requests_per_hour": need})
        report = {"symbols": n, "hourly_budget": hourly,
                  "hourly_demand": demand, "per_timeframe": rows,
                  "feasible": (hourly == 0 or demand <= hourly)}
        if hourly and demand > hourly:
            logger.error(
                "MARKET DATA OVER BUDGET: %d symbols x %s needs ~%d "
                "requests/hour but %s allows %d. Symbols will fall behind - "
                "reduce the universe, drop a timeframe, or add a provider.",
                n, "/".join(self.scheduler.timeframes), demand,
                self.source.name, hourly)
        elif hourly:
            logger.info("market-data budget: ~%d requests/hour needed of %d "
                        "available (%d symbols)", demand, hourly, n)
        self.state.rate_state = {**self.state.rate_state, "budget": report}
        return report

    # ------------------------------------------------------------- polling

    def poll(self, at: Optional[datetime] = None,
             held: Sequence[str] = (),
             now: Optional[float] = None) -> PollReport:
        """One bounded step: plan, then spend what the rate budget allows.

        Never sleeps and never raises. Returns without draining the queue when
        the budget is spent - that is the normal case for a large universe, and
        the caller simply polls again.
        """
        started = time.monotonic()
        now = time.monotonic() if now is None else now
        at = at or (self.clock.now() if self.clock is not None else None)
        report = PollReport()
        self.polls += 1

        try:
            report.due_timeframes = self.scheduler.due_timeframes(at)
            self._plan(at, held, now, report)
            self._dispatch(now, started, report)
        except Exception as exc:      # the feed must never abort the loop
            logger.exception("market-data poll failed: %s", exc)
            self.state.note_provider(self.source.name, DEGRADED,
                                     f"poll error: {exc}")

        report.completed = self._collect_completions(now)
        report.elapsed_s = time.monotonic() - started
        report.queue_depth = len(self.queue)
        report.wait_s = self.transport.candle_limiter.wait_time(
            time.monotonic())
        self._publish(report)
        return report

    def _plan(self, at, held: Sequence[str], now: float,
              report: PollReport) -> None:
        """Ask the scheduler what should exist, and queue it."""
        for tf in report.due_timeframes:
            self._pass_opened.setdefault(tf, now)
        requests = self.scheduler.plan(self.state, at, held=held)
        self.queue.extend(requests)
        if self.quotes_enabled and held and self._quotes_due(now):
            self._last_quote_at = now
            self.queue.extend(self.scheduler.plan_quotes(
                list(held), self.capabilities))

    def _quotes_due(self, now: float) -> bool:
        if self._last_quote_at is None:
            return True
        return (now - self._last_quote_at) >= self.quote_interval_s

    def _dispatch(self, now: float, started: float,
                  report: PollReport) -> None:
        """Spend the budget: pop, send, apply. Stops rather than waits."""
        while self.queue and report.sent < self.max_requests_per_poll:
            if (time.monotonic() - started) >= self.poll_budget_seconds:
                report.deferred = len(self.queue)
                break
            head = self.queue.peek()
            limiter = self.transport.limiter_for(head.kind)
            clock_now = time.monotonic()
            if not limiter.ready(clock_now):
                # not our turn yet: leave the work queued and hand the loop
                # back. Sleeping here is what coupled scheduling to waiting.
                limiter.defer()
                report.deferred = len(self.queue)
                break
            request = self.queue.pop()
            result = self.transport.execute(request, now=clock_now)
            report.sent += 1
            self._apply(request, result, report)

    # -------------------------------------------------------------- results

    def _apply(self, request: DataRequest, result: FetchResult,
               report: PollReport) -> None:
        """Fold one result into MarketState. The ONLY writer of market truth."""
        if request.kind == QUOTES:
            self._apply_quotes(result, report)
            return

        symbol, timeframe = request.symbol, request.timeframe

        if result.rate_limited:
            # NOT a symbol failure - the provider never looked at the symbol.
            # Re-queue so the bar is still served; the limiter has already
            # widened spacing, so the retry lands later than this one did.
            self.queue.push(request)
            report.requeued += 1
            self.state.note_provider(self.source.name, DEGRADED,
                                     "rate limited by provider")
            return

        if result.unavailable:
            self.state.record_failure(symbol, timeframe, result.error,
                                      unavailable=True,
                                      latency_s=result.latency_s)
            self.scheduler.mark_served(request, self.state, success=False)
            report.failed += 1
            return

        if not result.ok:
            self.state.record_failure(symbol, timeframe, result.error,
                                      latency_s=result.latency_s)
            self.scheduler.mark_served(request, self.state, success=False)
            report.failed += 1
            return

        rows = 0
        if result.frame is not None and not result.empty:
            rows = self.state.apply_candles(symbol, timeframe, result.frame)
        # An EMPTY window is a served request: the provider answered, and the
        # bar simply held no trades. Treating it as a failure would retry an
        # illiquid symbol until its attempts ran out, every bar, forever.
        self.state.record_success(symbol, timeframe, rows=rows,
                                  latency_s=result.latency_s)
        self.scheduler.mark_served(request, self.state, success=True)
        report.served += 1
        report.rows += rows

    def _apply_quotes(self, result: FetchResult, report: PollReport) -> None:
        if not result.ok:
            # marking is display-only: a failed quote leaves the last mark
            # standing and must never be escalated into a data fault
            logger.debug("quote request failed: %s", result.error)
            return
        for symbol, quote in result.quotes.items():
            self.state.quotes[symbol] = quote.price
        if result.quotes:
            self.state.quotes_ts = pd.Timestamp.now(tz="UTC")
        report.quotes += len(result.quotes)

    # ----------------------------------------------------------- completion

    def _collect_completions(self, now: float) -> List[str]:
        """Timeframes whose pass finished (or timed out) since the last ask.

        Drains the list: each completed bar is reported exactly once, so the
        caller scans once per bar rather than once per poll.
        """
        for tf in list(self._pass_opened):
            if self.scheduler.pending_count(tf) == 0:
                self._completed.append(tf)
                self._pass_opened.pop(tf, None)
            elif (self.scan_deadline_seconds > 0
                    and (now - self._pass_opened[tf])
                    >= self.scan_deadline_seconds):
                pending = self.scheduler.pending_count(tf)
                logger.warning(
                    "%s: scan deadline reached with %d symbol(s) still "
                    "unserved - scanning on what arrived (freshness will name "
                    "the laggards)", tf, pending)
                self._completed.append(tf)
                self._pass_opened.pop(tf, None)
        out, self._completed = self._completed, []
        return out

    def completed_timeframes(self) -> List[str]:
        out, self._completed = self._completed, []
        return out

    # ---------------------------------------------------------- observation

    def _publish(self, report: PollReport) -> None:
        """Refresh the parts of MarketState only the service can know."""
        mono = time.monotonic()
        self.state.queue_state = self.queue.snapshot()
        self.state.rate_state = {
            **self.state.rate_state,
            "candles": self.transport.candle_limiter.snapshot(mono),
            "quotes": self.transport.quote_limiter.snapshot(mono),
            "last_poll": report.to_dict(),
            "polls": self.polls,
        }
        for tf in self.scheduler.timeframes:
            self.state.tf_health(tf).pending = self.scheduler.pending_count(tf)
        self._assess_provider(report)

    def _assess_provider(self, report: PollReport) -> None:
        """Provider status from OBSERVED behaviour, not from a config flag.

        A source that declares itself OFFLINE is taken at its word; otherwise
        the verdict comes from what requests actually did. Consecutive polls in
        which every attempt failed mean the provider is gone even though each
        individual symbol looks like its own small problem.
        """
        declared = self.source.status()
        if declared == OFFLINE:
            self.state.note_provider(self.source.name, OFFLINE,
                                     "source reports offline")
            return
        if report.sent == 0:
            if self.state.provider_status == PENDING and not report.deferred:
                self.state.note_provider(self.source.name, PENDING,
                                         "nothing due yet")
            return
        if report.served == 0 and report.failed > 0:
            self._blind_polls += 1
            if self._blind_polls >= 3:
                self.state.note_provider(
                    self.source.name, OFFLINE,
                    f"{self._blind_polls} consecutive polls served nothing")
                return
            self.state.note_provider(self.source.name, DEGRADED,
                                     "recent requests are failing")
            return
        self._blind_polls = 0
        if report.failed:
            self.state.note_provider(
                self.source.name, DEGRADED,
                f"{report.failed} of {report.sent} request(s) failed")
        else:
            self.state.note_provider(self.source.name, OK, "serving")

    # ------------------------------------------------------------- pacing

    def drain(self, at: Optional[datetime] = None, held: Sequence[str] = (),
              deadline_s: float = 120.0, sleep=time.sleep) -> PollReport:
        """Poll until the queue empties or ``deadline_s`` elapses.

        For ONE-SHOT contexts only - ``--once``, startup warm-up, benchmarks -
        where there is no loop to return to. The live loop must never call this:
        blocking for the ~33 seconds a 99-symbol pass takes would suspend
        square-off and kill-switch checks for that whole window, which is the
        coupling ``poll`` exists to break.

        It is the same service, queue and limiter as the live path - not a
        second fetcher. It simply supplies the loop that the caller lacks.
        """
        started = time.monotonic()
        total = PollReport()
        while True:
            report = self.poll(at=at, held=held)
            total.sent += report.sent
            total.served += report.served
            total.failed += report.failed
            total.rows += report.rows
            total.requeued += report.requeued
            total.quotes += report.quotes
            total.completed.extend(report.completed)
            # Done only when a poll BOTH sent nothing AND left nothing queued.
            # An empty queue alone is not completion: a failed symbol is
            # re-planned on the next poll for its second attempt, so breaking
            # the instant the queue drains would leave those symbols pending
            # forever in one-shot mode. A poll that sends nothing with an empty
            # queue means the scheduler has no more work to emit.
            if report.sent == 0 and not self.queue:
                break
            elapsed = time.monotonic() - started
            if elapsed >= deadline_s:
                logger.warning("drain deadline reached after %.1fs with %d "
                               "request(s) still queued", elapsed,
                               len(self.queue))
                break
            wait = self.transport.candle_limiter.wait_time(time.monotonic())
            if wait > 0:
                sleep(min(wait, max(0.0, deadline_s - elapsed)))
        total.elapsed_s = time.monotonic() - started
        total.queue_depth = len(self.queue)
        return total

    def seconds_until_next(self, at: Optional[datetime] = None) -> float:
        """Loop sleep hint. Zero while there is work the budget will allow.

        The caller must not sleep past outstanding work: when the queue holds
        requests, the wait is the rate limiter's, not the next bar's.
        """
        if self.queue:
            return min(1.0, self.transport.candle_limiter.wait_time(
                time.monotonic()))
        return self.scheduler.seconds_until_next(at)

    def snapshot(self, at: Optional[datetime] = None) -> dict:
        """Everything an operator or dashboard needs about the feed."""
        return {
            "provider": self.source.name,
            "status": self.state.provider_status,
            "detail": self.state.provider_detail,
            "capabilities": {
                "candle_symbols_per_request":
                    self.capabilities.candle_symbols_per_request,
                "quote_symbols_per_request":
                    self.capabilities.quote_symbols_per_request,
                "streaming": self.capabilities.supports_streaming,
                "hourly_candle_budget":
                    self.capabilities.hourly_candle_budget(),
            },
            "scheduler": self.scheduler.snapshot(at),
            "queue": self.queue.snapshot(),
            "transport": self.transport.snapshot(),
            "latency_ms": self.state.latency_ms,
            "latency_p95_ms": self.state.latency_percentile(95),
        }


#: THE timeframe->minutes definition, shared with the data layer. A local
#: parser here would be a third copy of the same table, and the two that
#: already existed disagreed with it about "1d" (they read the "d" as minutes).
_tf_minutes = ohlcv.timeframe_minutes
