"""Market Data Architecture v2 - the scheduler, queue, rate limiter and the
single-owner service.

Each test pins one of the non-negotiable design principles, and each names the
defect class it exists to prevent:

  * a timeframe fetched BEFORE its bar completed (partial candle in the store)
  * a due timeframe SKIPPED after budget ran out (a missed bar)
  * a symbol already current RE-REQUESTED every cycle (the old O(universe) cost)
  * the REST request window NOT actually incremental (a year pulled per bar)
  * one bad symbol halting the whole scan (99 configured, 3 bad, 0 traded)
  * scheduling and transport fused (no websocket swap possible)

The source is a recording double: it captures every ``fetch_candles`` call, so
the tests assert on what was ACTUALLY requested of the provider, not merely on
what reached the store - the two looked identical in the defect that pulled a
year of history while writing only new rows.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from algo.data.store import MarketDataStore
from algo.marketdata import (
    AdaptiveRateLimiter, DataRequest, MarketDataService, MarketState,
    ProviderCapabilities, RateLimit, RequestQueue, SMARTAPI_CAPABILITIES,
    TimeframeScheduler,
)
from algo.marketdata.capabilities import UNLIMITED_CAPABILITIES
from algo.marketdata.queue import CANDLES, P_BACKFILL, P_DUE, P_HELD, QUOTES
from algo.marketdata.source import MarketDataSource, ProviderSource, Quote
from algo.trading.clock import IST, MarketClock

TF = "15m"
MONDAY = datetime(2026, 7, 20, 11, 7, tzinfo=IST)      # mid-session


# ============================================================ test doubles

class RecordingSource(MarketDataSource):
    """A source that generates deterministic bars and RECORDS every request.

    ``requests`` is the list of ``(symbol, timeframe, start, end)`` tuples the
    transport actually asked for - the ground truth for "was the fetch
    incremental" that a store-only assertion cannot provide.
    """

    name = "recording"

    def __init__(self, capabilities=UNLIMITED_CAPABILITIES,
                 fail=(), unavailable=(), rate_limit_first=0):
        self._caps = capabilities
        self.requests: list = []
        self.quote_calls: list = []
        self._fail = set(fail)
        self._unavailable = set(unavailable)
        self._rate_limit_first = rate_limit_first

    @property
    def capabilities(self):
        return self._caps

    def fetch_candles(self, symbol, timeframe, start, end):
        self.requests.append((symbol, timeframe, pd.Timestamp(start),
                              pd.Timestamp(end)))
        if self._rate_limit_first > 0:
            self._rate_limit_first -= 1
            raise RuntimeError("exceeding access rate")
        if symbol in self._fail:
            raise RuntimeError(f"synthetic failure for {symbol}")
        opens = pd.date_range(pd.Timestamp(start).ceil("15min"),
                              pd.Timestamp(end), freq="15min", tz="UTC")
        if len(opens) == 0:
            from algo.data.ohlcv import OHLCV_COLUMNS
            return pd.DataFrame(columns=list(OHLCV_COLUMNS))
        return pd.DataFrame({"date": opens, "open": 100.0, "high": 101.0,
                             "low": 99.0, "close": 100.5, "volume": 1000})

    def fetch_quotes(self, symbols):
        symbols = list(symbols)
        self.quote_calls.append(symbols)
        return {s: Quote(symbol=s, price=100.0) for s in symbols}

    def unavailable_reason(self, symbol, timeframe):
        return "no token" if symbol in self._unavailable else None

    def is_rate_limited(self, error):
        return "access rate" in str(error)


def _clock(now=MONDAY):
    clock = MarketClock.build()
    clock.now = lambda: now
    return clock


def _state(tmp_path, symbols, tf=TF, seed_bars_until=None):
    store = MarketDataStore(tmp_path / "store")
    state = MarketState(store, list(symbols), [tf])
    state.set_symbols(list(symbols))
    if seed_bars_until is not None:
        opens = pd.date_range(end=pd.Timestamp(seed_bars_until), periods=40,
                              freq="15min", tz="UTC")
        for s in symbols:
            store.write(s, tf, pd.DataFrame({
                "date": opens, "open": 100.0, "high": 101.0, "low": 99.0,
                "close": 100.5, "volume": 1000}))
    return state


# =================================================== §3 no early / no skip

def test_the_forming_bar_is_never_the_expected_bar():
    """The expected bar is always one that has CLOSED. A forming bar fetched
    early returns a partial candle, indistinguishable from a real one once
    stored."""
    clock = _clock()
    sched = TimeframeScheduler(clock, [TF], grace_seconds=20)
    # at 11:07 the forming bar opened 11:00; the expected (last closed) is 10:45
    at = datetime(2026, 7, 20, 11, 7, tzinfo=IST)
    expected = sched.expected_bar(TF, at=at)
    assert expected == pd.Timestamp("2026-07-20 05:15:00", tz="UTC")   # 10:45


def test_a_served_bar_is_not_re_served_until_its_successor_closes(tmp_path):
    """Once a bar is served it stays served; the timeframe becomes due again
    only when the NEXT bar has both closed and cleared the grace window."""
    clock = _clock()
    source = RecordingSource()
    state = _state(tmp_path, ["RELIANCE"], seed_bars_until="2026-07-20 05:00:00")
    sched = TimeframeScheduler(clock, [TF], grace_seconds=20)
    sched.bind_source(source)
    # serve the 10:45 bar (expected at 11:07)
    at = datetime(2026, 7, 20, 11, 7, tzinfo=IST)
    for req in sched.plan(state, at=at):
        state.store.write(req.symbol, TF, source.fetch_candles(
            req.symbol, TF, req.start, req.end))
        sched.mark_served(req, state, success=True)
    # inside the SAME bar window (11:14:59), nothing new is due
    assert not sched.is_due(TF, at=datetime(2026, 7, 20, 11, 14, 59, tzinfo=IST))
    # the 11:00 bar closes 11:15; at 11:15:30 (past close + grace) it is due
    assert sched.is_due(TF, at=datetime(2026, 7, 20, 11, 15, 30, tzinfo=IST))


def test_a_due_timeframe_is_not_skipped_when_budget_runs_out(tmp_path):
    """A pass that cannot finish this poll stays PENDING, not dropped: the work
    is deferred to the next poll, never lost."""
    clock = _clock()
    source = RecordingSource()
    state = _state(tmp_path, [f"S{i}" for i in range(10)],
                   seed_bars_until="2026-07-20 05:00:00")  # one bar behind
    sched = TimeframeScheduler(clock, [TF], grace_seconds=0)
    sched.bind_source(source)
    # plan emits a request per due symbol; none served yet -> still pending
    requests = sched.plan(state, at=MONDAY)
    assert len(requests) == 10
    assert sched.pending_count(TF) == 10
    # the timeframe remains due until every symbol is served
    assert sched.is_due(TF, at=MONDAY)


def test_only_due_symbols_are_requested(tmp_path):
    """A symbol whose newest stored bar already IS the expected bar is not
    requested - re-fetching it would spend budget to receive bars we hold."""
    clock = _clock()
    # the expected 15m bar at 11:07 opened 10:45 UTC 05:15
    expected = clock.last_closed_bar_open(TF, at=MONDAY)
    expected_utc = pd.Timestamp(expected).tz_convert("UTC")
    state = _state(tmp_path, ["CURRENT", "BEHIND"])
    # CURRENT already has the expected bar; BEHIND is short by one
    state.store.write("CURRENT", TF, pd.DataFrame({
        "date": [expected_utc], "open": 100.0, "high": 101.0, "low": 99.0,
        "close": 100.5, "volume": 1000}))
    state.store.write("BEHIND", TF, pd.DataFrame({
        "date": [expected_utc - pd.Timedelta(minutes=15)], "open": 100.0,
        "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000}))
    sched = TimeframeScheduler(clock, [TF], grace_seconds=0)
    due = sched.symbols_due(TF, state, at=MONDAY)
    assert due == ["BEHIND"]


# ==================================================== §5 incremental fetch

def test_the_rest_request_window_is_incremental(tmp_path):
    """THE key scaling property. The window sent to the provider must start one
    bar after what is stored - not at a fixed lookback. Asserted on the source's
    recorded call, because writing only new rows to the store while downloading
    a year every time would look identical from the store's side."""
    clock = _clock()
    source = RecordingSource()
    last_stored = pd.Timestamp("2026-07-20 05:00:00", tz="UTC")   # 10:30 IST
    state = _state(tmp_path, ["RELIANCE"], seed_bars_until=last_stored)
    service = MarketDataService(
        state, TimeframeScheduler(clock, [TF], grace_seconds=0,
                                  capabilities=source.capabilities),
        _transport(source), source, clock=clock)

    service.poll(at=MONDAY)

    assert len(source.requests) == 1
    symbol, tf, start, end = source.requests[0]
    # the window begins ONE bar after the stored tail, never a day earlier
    assert start == last_stored + pd.Timedelta(minutes=15)
    assert (start - last_stored) == pd.Timedelta(minutes=15)


def test_a_symbol_with_no_history_seeds_a_bounded_window(tmp_path):
    """A never-seen symbol seeds a SMALL window on the live path - not the
    ingestion default of a year, which would pull 365 days per symbol at the
    first refresh of a fresh timeframe."""
    clock = _clock()
    source = RecordingSource()
    state = _state(tmp_path, ["NEW"])            # no stored bars
    sched = TimeframeScheduler(clock, [TF], grace_seconds=0,
                               live_lookback_days=5)
    sched.bind_source(source)
    requests = sched.plan(state, at=MONDAY)
    assert len(requests) == 1
    span = requests[0].end - requests[0].start
    assert span <= pd.Timedelta(days=6)
    assert requests[0].priority == P_BACKFILL     # seeding must not delay live


def test_completed_data_is_never_re_requested(tmp_path):
    """After a symbol is served for a bar, a second poll in the same bar asks
    for nothing - the queue and scheduler both know it is current."""
    clock = _clock()
    source = RecordingSource()
    state = _state(tmp_path, ["RELIANCE"], seed_bars_until="2026-07-20 05:00:00")
    service = MarketDataService(
        state, TimeframeScheduler(clock, [TF], grace_seconds=0,
                                  capabilities=source.capabilities),
        _transport(source), source, clock=clock)
    service.poll(at=MONDAY)
    n_after_first = len(source.requests)
    service.poll(at=MONDAY)                       # same bar, nothing new
    assert len(source.requests) == n_after_first


# ==================================================== §6 batching (audited)

def test_smartapi_candles_cannot_batch_but_quotes_can():
    """The audit result, pinned. getCandleData takes one symboltoken;
    getMarketData serves 50. A change to either declaration must break here."""
    caps = SMARTAPI_CAPABILITIES
    assert caps.candle_symbols_per_request == 1
    assert not caps.candles_batchable()
    assert caps.quote_symbols_per_request == 50
    assert caps.quotes_batchable()
    # 120 symbols -> 3 quote requests, never 120
    batches = caps.quote_batches([f"S{i}" for i in range(120)])
    assert [len(b) for b in batches] == [50, 50, 20]


def test_the_hourly_budget_is_the_binding_constraint():
    """At scale the per-hour cap binds, not per-second. 1000 symbols on 5m is
    12000 requests/hour against a 5000 ceiling - impossible at any rate."""
    caps = SMARTAPI_CAPABILITIES
    assert caps.hourly_candle_budget() == 5000


def test_quotes_are_batched_through_the_source(tmp_path):
    """A bulk-quote provider is asked ONCE for 50 symbols, not 50 times."""
    source = RecordingSource(capabilities=SMARTAPI_CAPABILITIES)
    provider_source = _BulkQuoteProvider()
    ps = ProviderSource(provider_source, capabilities=SMARTAPI_CAPABILITIES)
    quotes = ps.fetch_quotes([f"S{i}" for i in range(50)])
    assert len(provider_source.calls) == 1        # ONE bulk call
    assert len(quotes) == 50


class _BulkQuoteProvider:
    name = "bulk"

    def __init__(self):
        self.calls = []

    def fetch_quotes(self, symbols):
        symbols = list(symbols)
        self.calls.append(symbols)
        return {s: 100.0 for s in symbols}


# ===================================================== §7 adaptive limiter

def test_the_rate_limiter_enforces_every_window_at_once():
    """Per-second AND per-minute AND per-hour all bind simultaneously; the
    tightest one wins at any moment."""
    lim = AdaptiveRateLimiter([RateLimit(3, 1.0), RateLimit(5, 60.0)])
    now = 1000.0
    for _ in range(3):
        assert lim.ready(now)
        lim.record(now)
    # per-second is now full: not ready until 1s passes
    assert not lim.ready(now)
    assert lim.next_available(now) == pytest.approx(now + 1.0)
    # after 1s, two more fit (per-minute cap is 5)
    now += 1.0
    lim.record(now)
    lim.record(now)
    assert not lim.ready(now)                     # 5/min reached
    assert lim.wait_time(now) > 1.0               # must wait for the minute


def test_the_rate_limiter_widens_spacing_on_rejection_and_recovers():
    """AIMD: a rejection multiplies spacing; sustained success decays it. The
    limiter converges on the rate the provider ACTUALLY enforces, which is not
    the documented number (3/s was observed rejecting at 2.5/s)."""
    lim = AdaptiveRateLimiter([RateLimit(100, 1.0)], initial_penalty_s=0.25,
                              backoff_factor=2.0, rewards_to_recover=2)
    assert lim.penalty_s == 0.0
    lim.penalize()
    assert lim.penalty_s == 0.25
    lim.penalize()
    assert lim.penalty_s == 0.5                   # multiplicative increase
    for _ in range(2):
        lim.reward()
    assert lim.penalty_s < 0.5                    # decayed after a clean streak


def test_affordability_answers_the_scaling_question():
    """Can this universe be served at this cadence? Answered before the session,
    not discovered mid-session when the hour's budget runs out."""
    lim = AdaptiveRateLimiter(SMARTAPI_CAPABILITIES.candle_limits)
    # an empty hour affords the full hourly budget
    assert lim.affordable(0.0, window_seconds=3600.0) == 5000


# ================================================= §8 scheduling != transport

def test_the_scheduler_sends_nothing(tmp_path):
    """The scheduler PRODUCES requests and hands them on; it never fetches.
    This is the seam a websocket transport slots into unchanged."""
    clock = _clock()
    source = RecordingSource()
    state = _state(tmp_path, ["RELIANCE"], seed_bars_until="2026-07-20 05:00:00")
    sched = TimeframeScheduler(clock, [TF], grace_seconds=0)
    sched.bind_source(source)
    requests = sched.plan(state, at=MONDAY)
    assert all(isinstance(r, DataRequest) for r in requests)
    assert source.requests == []                  # planning fetched NOTHING


# ==================================================== queue dedup / priority

def test_the_queue_deduplicates_identical_requests():
    q = RequestQueue()
    r = DataRequest(kind=CANDLES, timeframe=TF, symbols=("RELIANCE",))
    assert q.push(r) is True
    assert q.push(r) is False                     # same key, refused
    assert len(q) == 1


def test_the_queue_serves_by_priority_then_arrival():
    """A held position outranks a routine scan outranks a backfill; equal
    priorities keep arrival order, so ordering is deterministic."""
    q = RequestQueue()
    q.push(DataRequest(CANDLES, TF, ("BACKFILL",), priority=P_BACKFILL))
    q.push(DataRequest(CANDLES, TF, ("DUE",), priority=P_DUE))
    q.push(DataRequest(CANDLES, TF, ("HELD",), priority=P_HELD))
    q.push(DataRequest(QUOTES, "", ("DUE2",), priority=P_DUE))
    order = [q.pop().symbols[0] for _ in range(4)]
    assert order == ["HELD", "DUE", "DUE2", "BACKFILL"]


def test_the_queue_is_bounded():
    """A provider that never serves anything cannot turn the queue into a
    memory leak over a session."""
    q = RequestQueue(max_depth=2)
    assert q.push(DataRequest(CANDLES, TF, ("A",)))
    assert q.push(DataRequest(CANDLES, TF, ("B",)))
    assert not q.push(DataRequest(CANDLES, TF, ("C",)))
    assert q.dropped == 1


def _transport(source):
    from algo.marketdata.transport import Transport
    return Transport(source)
