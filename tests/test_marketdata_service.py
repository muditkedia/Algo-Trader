"""Market Data Architecture v2 - the service (single owner), MarketState (single
truth), symbol-level isolation, and provider abstraction.

The service is the ONE object that fetches. These tests pin that:

  * a poll is BOUNDED - a 99-symbol pass does not block the caller's loop
  * three bad symbols out of ninety-nine cost three, never the scan
  * an offline source serves stored candles, never an empty "quiet market"
  * MarketState is the single source of truth: freshness, health and marks all
    come from it, and swapping the source under it changes nothing a consumer
    can see (the websocket/Zerodha/replay seam)
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from algo.data.store import MarketDataStore
from algo.marketdata import (
    MarketDataService,
    NullSource,
    RateLimit,
    SMARTAPI_CAPABILITIES)
from algo.marketdata.capabilities import (
    ProviderCapabilities, UNLIMITED_CAPABILITIES,
)
from algo.marketdata.source import MarketDataSource
from algo.trading.clock import IST, MarketClock

TF = "15m"
MONDAY = datetime(2026, 7, 20, 11, 7, tzinfo=IST)


class FakeSource(MarketDataSource):
    """Generates bars, and can be told which symbols fail or are unavailable."""

    name = "fake"

    def __init__(self, capabilities=UNLIMITED_CAPABILITIES, fail=(),
                 unavailable=(), offline=False):
        self._caps = capabilities
        self._fail = set(fail)
        self._unavailable = set(unavailable)
        self._offline = offline
        self.requests = []

    @property
    def capabilities(self):
        return self._caps

    def fetch_candles(self, symbol, timeframe, start, end):
        self.requests.append(symbol)
        if symbol in self._fail:
            raise RuntimeError(f"boom {symbol}")
        opens = pd.date_range(pd.Timestamp(start).ceil("15min"),
                              pd.Timestamp(end), freq="15min", tz="UTC")
        if len(opens) == 0:
            from algo.data.ohlcv import OHLCV_COLUMNS
            return pd.DataFrame(columns=list(OHLCV_COLUMNS))
        return pd.DataFrame({"date": opens, "open": 100.0, "high": 101.0,
                             "low": 99.0, "close": 100.5, "volume": 1000})

    def unavailable_reason(self, symbol, timeframe):
        return "no token" if symbol in self._unavailable else None

    def status(self):
        return "OFFLINE" if self._offline else "OK"


def _clock(now=MONDAY):
    clock = MarketClock.build()
    clock.now = lambda: now
    return clock


def _service(tmp_path, symbols, source, seed_until="2026-07-20 05:00:00",
             **kw):
    store = MarketDataStore(tmp_path / "store")
    if seed_until is not None:
        opens = pd.date_range(end=pd.Timestamp(seed_until, tz="UTC"),
                              periods=40, freq="15min", tz="UTC")
        for s in symbols:
            store.write(s, TF, pd.DataFrame({
                "date": opens, "open": 100.0, "high": 101.0, "low": 99.0,
                "close": 100.5, "volume": 1000}))
    clock = _clock()
    return MarketDataService.build(store, source, clock, symbols, [TF], **kw)


# ================================================= §10 data-quality isolation

def test_three_bad_symbols_out_of_ninetynine_do_not_halt_the_pass(tmp_path):
    """The headline principle: 99 configured, 3 failing, the pass still
    completes and serves the other 96. A handful of bad symbols is a per-symbol
    condition tracked in health, never a feed-wide halt."""
    symbols = [f"S{i}" for i in range(99)]
    bad = {"S3", "S30", "S60"}
    source = FakeSource(fail=bad)
    service = _service(tmp_path, symbols, source,
                       max_requests_per_poll=400, poll_budget_seconds=30)
    service.drain(deadline_s=5, sleep=lambda s: None)
    # the pass reached completion despite the failures - nothing hangs pending
    assert service.scheduler.pending_count(TF) == 0
    # the 96 good symbols served fresh bars; the 3 bad ones carry a recorded
    # failure (symbol-level health maintained) but did not stop the rest
    served = [s for s in symbols if service.state.health_for(s).total_successes]
    failed = [s for s in symbols if service.state.health_for(s).total_failures]
    assert len(served) == 96
    assert set(failed) == bad


def test_a_partial_failure_still_scans_the_fresh_survivors(tmp_path):
    """Isolation surfaces where it matters: freshness names exactly the symbols
    behind, so the operator/scan can tell the 96 fresh from the 3 stale."""
    symbols = ["GOOD1", "GOOD2", "BAD"]
    source = FakeSource(fail={"BAD"})
    # seed several bars back so a symbol that cannot update is clearly stale
    # (more than the 1-bar tolerance behind the expected 10:45 bar)
    service = _service(tmp_path, symbols, source, poll_budget_seconds=30,
                       max_requests_per_poll=50, seed_until="2026-07-20 03:00:00")
    service.drain(deadline_s=5, sleep=lambda s: None)
    report = service.state.freshness(TF, clock=service.clock)
    fresh = {s.symbol for s in report.fresh}
    stale = {s.symbol for s in report.stale}
    assert "GOOD1" in fresh and "GOOD2" in fresh
    assert stale == {"BAD"}                       # the one that could not update


def test_an_unavailable_symbol_is_never_even_requested(tmp_path):
    """A symbol with no instrument token cannot be addressed. It is set aside
    up front, not fetched-and-failed, so it costs nothing and reads as an
    operator problem rather than a quiet market (D-038)."""
    source = FakeSource(unavailable={"NOEXIST"})
    service = _service(tmp_path, ["REAL", "NOEXIST"], source,
                       poll_budget_seconds=30)
    service.drain(deadline_s=5, sleep=lambda s: None)
    assert "NOEXIST" not in source.requests
    assert service.state.health_for("NOEXIST").status == "UNAVAILABLE"


# ============================================================ §1 bounded poll

def test_a_poll_is_bounded_and_does_not_drain_a_large_pass(tmp_path):
    """A 99-symbol pass at ~3 req/s takes ~33s and CANNOT complete in one poll.
    The poll spends the budget and returns; the caller is never blocked for the
    whole pass while square-off waits."""
    symbols = [f"S{i}" for i in range(99)]
    source = FakeSource(capabilities=SMARTAPI_CAPABILITIES)
    service = _service(tmp_path, symbols, source,
                       max_requests_per_poll=5, poll_budget_seconds=30)
    report = service.poll(at=MONDAY)
    # at most the per-poll cap was sent, and work remains queued
    assert report.sent <= 5
    assert len(service.queue) > 0


def test_the_poll_never_blocks_on_the_rate_limiter(tmp_path):
    """When the limiter says 'not yet', the poll returns rather than sleeping -
    scheduling and waiting are separate acts, so a due timeframe cannot hold
    the loop that square-off needs."""
    symbols = [f"S{i}" for i in range(10)]
    # a limiter with zero immediate capacity
    source = FakeSource(capabilities=ProviderCapabilities(
        name="slow", candle_limits=(RateLimit(1, 3600.0),)))
    service = _service(tmp_path, symbols, source, poll_budget_seconds=30)
    service.poll(at=MONDAY)                       # spends the 1 token
    import time
    t0 = time.monotonic()
    report = service.poll(at=MONDAY)              # limiter now empty
    assert time.monotonic() - t0 < 0.5            # returned, did not sleep
    assert report.sent == 0
    assert report.wait_s > 0                      # and says when to come back


# ================================================= §12 single source of truth

def test_marketstate_is_the_only_market_writer(tmp_path):
    """Candles enter the store ONLY through the service applying a fetch. After
    a drain, MarketState's history reflects exactly what arrived."""
    source = FakeSource()
    service = _service(tmp_path, ["RELIANCE"], source,
                       seed_until="2026-07-20 04:00:00")   # several bars behind
    before = len(service.state.history("RELIANCE", TF))
    service.drain(deadline_s=5, sleep=lambda s: None)
    after = len(service.state.history("RELIANCE", TF))
    assert after > before                        # new bars were applied
    assert service.state.tf_health(TF).rows_added > 0


def test_freshness_comes_from_state_not_recomputed(tmp_path):
    """Every consumer reads MarketState.freshness - there is one implementation
    and one answer, so two panels cannot disagree about staleness."""
    source = FakeSource()
    service = _service(tmp_path, ["RELIANCE"], source)
    service.drain(deadline_s=5, sleep=lambda s: None)
    report = service.state.freshness(TF, clock=service.clock)
    assert report.total == 1
    assert report.status in ("FRESH", "STALE", "MARKET_CLOSED")


# ================================================= §15 provider abstraction

def test_swapping_the_source_changes_nothing_a_consumer_sees(tmp_path):
    """The websocket/Zerodha/replay seam. Two different sources, same stored
    outcome and same MarketState surface - a consumer cannot tell them apart."""
    from algo.trading.orchestrator import Orchestrator

    def run(source):
        svc = _service(tmp_path / str(id(source)), ["RELIANCE"], source,
                       seed_until="2026-07-20 04:00:00")
        svc.drain(deadline_s=5, sleep=lambda s: None)
        orch = Orchestrator([], svc.state)
        return (orch._symbols(TF), svc.state.data_ok(TF),
                len(svc.state.history("RELIANCE", TF)) > 0)

    rest = FakeSource()                          # unlimited/no-batch profile
    bulk = FakeSource(capabilities=SMARTAPI_CAPABILITIES)   # batched quotes
    assert run(rest) == run(bulk)                # identical consumer-visible state


def test_offline_source_serves_stored_candles_not_a_quiet_market(tmp_path):
    """A NullSource (no provider wired) must NOT empty the watchlist or fail
    the fetch - offline is a mode, and the stored bars are still real. This is
    what lets paper trading run the real pipeline without credentials."""
    service = _service(tmp_path, ["RELIANCE", "TCS"], NullSource(),
                       seed_until="2026-07-20 05:00:00")
    report = service.poll(at=MONDAY)
    # no requests are sent, no symbol is marked bad, the scan set is intact
    assert service.state.usable_symbols(TF) == ["RELIANCE", "TCS"]
    diag = service.state.diagnosis(TF)
    assert diag["ok"] and diag["offline"]
    assert "offline" in diag["headline"].lower()


# ======================================================= §14 feasibility gate

def test_the_budget_check_flags_an_over_capacity_universe(tmp_path):
    """1000 symbols on 5m needs ~12000 requests/hour against a 5000 ceiling.
    The operator learns this at STARTUP, not at 12:30 when the hour runs out."""
    store = MarketDataStore(tmp_path / "store")
    source = FakeSource(capabilities=SMARTAPI_CAPABILITIES)
    clock = _clock()
    symbols = [f"S{i}" for i in range(1000)]
    service = MarketDataService.build(store, source, clock, symbols, ["5m"])
    report = service.check_budget()
    assert not report["feasible"]
    assert report["hourly_demand"] > report["hourly_budget"] > 0


def test_a_reasonable_universe_is_within_budget(tmp_path):
    store = MarketDataStore(tmp_path / "store")
    source = FakeSource(capabilities=SMARTAPI_CAPABILITIES)
    clock = _clock()
    service = MarketDataService.build(store, source, clock,
                                      [f"S{i}" for i in range(99)], [TF])
    report = service.check_budget()
    assert report["feasible"]


# ========================================= §9 multi-timeframe intelligence

def test_the_scheduler_owns_only_strategy_declared_timeframes(tmp_path):
    """A timeframe no enabled strategy trades is NEVER fetched. The engine
    derives the live set from the strategies, and the market-data scheduler is
    built from that same list - so 1h is absent when nothing uses it."""
    from algo.trading.config import TradingConfig
    from algo.trading.engine import ProductionEngine
    from algo.strategies.library import OpeningRangeBreakout   # a 5m strategy

    (tmp_path / "syms.txt").write_text("RELIANCE\n")
    cfg = TradingConfig.from_dict({
        "mode": "paper", "symbols_file": str(tmp_path / "syms.txt"),
        "store_dir": str(tmp_path / "store"), "state_dir": str(tmp_path / "s"),
        "dashboard_dir": str(tmp_path / "dash")})
    eng = ProductionEngine(cfg, strategies=[OpeningRangeBreakout()])
    assert eng.marketdata.scheduler.timeframes == ["5m"]
    assert "1h" not in eng.marketdata.scheduler.timeframes


# ============================================ §17 exactly one owner of updates

def test_a_wired_provider_supplies_the_instrument_mapping(tmp_path):
    """MarketState resolves symbols through the SOURCE's instrument master, so
    a wired provider is never reported as 'offline, no mapping' (the token panel
    and preflight both read this)."""
    class Instruments:
        def resolve_many(self, symbols):
            return {"resolved": len(symbols), "total": len(symbols)}

    class MappedSource(FakeSource):
        def mapping_report(self):
            return Instruments()

    service = _service(tmp_path, ["RELIANCE"], MappedSource())
    assert service.state.mapping is not None
    assert service.state.mapping_report() == {"resolved": 1, "total": 1}


def test_the_service_holds_the_only_fetch_path(tmp_path):
    """There is one place candles are requested. The transport records every
    call; nothing outside the service can have produced them."""
    source = FakeSource()
    service = _service(tmp_path, ["RELIANCE"], source,
                       seed_until="2026-07-20 04:00:00")
    assert source.requests == []                 # nothing fetched at build
    service.drain(deadline_s=5, sleep=lambda s: None)
    # every recorded request is for the one watchlist symbol - no rogue fetch
    assert set(source.requests) == {"RELIANCE"}
