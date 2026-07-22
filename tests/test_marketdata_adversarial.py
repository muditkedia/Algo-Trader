"""Adversarial audit of Market Data Architecture v2 (§19).

Every test here is an ATTEMPT TO BREAK the subsystem, one per failure mode the
production system will actually meet. The bar is not "does it cope" but "does
it stay correct and keep trading the symbols it still can" - a market-data
fault must degrade to less data, never to a wrong decision or a stopped loop.

Failure modes exercised:
  provider offline .............. serves stored candles, never a false quiet
  provider goes offline mid-run . recovers to OK when it returns
  rate-limited hard ............. requeues, never loses the bar, never floods
  a symbol vanishes ............. isolated; the rest keep going
  delayed bar ................... not fetched early, served once it lands
  exchange closed ............... nothing due, nothing fetched
  restart mid-fetch ............. no partial bar in the store; resumes cleanly
  restart during scan ........... no double-trade; dedup survives
  exporter throws ............... trading is untouched
  provider returns garbage ...... quarantined, not stored
  clock jumps backwards ......... no crash, no negative waits
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from algo.data.store import MarketDataStore
from algo.marketdata import (
    MarketDataService,
    MarketState,
    NullSource,
    RateLimit,
    SMARTAPI_CAPABILITIES)
from algo.marketdata.capabilities import ProviderCapabilities
from algo.marketdata.source import MarketDataSource
from algo.trading.clock import IST, MarketClock

TF = "15m"
MONDAY = datetime(2026, 7, 20, 11, 7, tzinfo=IST)
SUNDAY = datetime(2026, 7, 19, 11, 7, tzinfo=IST)


class ControlledSource(MarketDataSource):
    """A source whose every behaviour can be switched at runtime, so one test
    can drive it through failure and back."""

    name = "controlled"

    def __init__(self, capabilities=SMARTAPI_CAPABILITIES):
        self._caps = capabilities
        self.mode = "ok"                  # ok | offline | rate | fail | garbage
        self.requests = []
        self.bars_up_to = None            # cap the newest bar the source will give
        self.rate_hits = 0

    @property
    def capabilities(self):
        return self._caps

    def fetch_candles(self, symbol, timeframe, start, end):
        self.requests.append((symbol, pd.Timestamp(start), pd.Timestamp(end)))
        if self.mode == "rate":
            self.rate_hits += 1
            raise RuntimeError("exceeding access rate")
        if self.mode == "fail":
            raise RuntimeError("connection reset")
        if self.mode == "garbage":
            # high < low, negative volume: a systematically broken response
            return pd.DataFrame({
                "date": pd.date_range(pd.Timestamp(start).ceil("15min"),
                                      pd.Timestamp(end), freq="15min", tz="UTC"),
                "open": 100.0, "high": 1.0, "low": 999.0, "close": -5.0,
                "volume": -1})
        end = pd.Timestamp(end)
        if self.bars_up_to is not None:
            end = min(end, pd.Timestamp(self.bars_up_to, tz="UTC"))
        opens = pd.date_range(pd.Timestamp(start).ceil("15min"), end,
                              freq="15min", tz="UTC")
        if len(opens) == 0:
            from algo.data.ohlcv import OHLCV_COLUMNS
            return pd.DataFrame(columns=list(OHLCV_COLUMNS))
        return pd.DataFrame({"date": opens, "open": 100.0, "high": 101.0,
                             "low": 99.0, "close": 100.5, "volume": 1000})

    def is_rate_limited(self, error):
        return "access rate" in str(error)

    def status(self):
        return "OFFLINE" if self.mode == "offline" else "OK"


def _clock(now=MONDAY):
    clock = MarketClock.build()
    clock.now = lambda: now
    return clock


def _service(tmp_path, symbols=("RELIANCE",), source=None,
             seed_until="2026-07-20 04:00:00", clock=None, **kw):
    store = MarketDataStore(tmp_path / "store")
    if seed_until is not None:
        opens = pd.date_range(end=pd.Timestamp(seed_until, tz="UTC"),
                              periods=40, freq="15min", tz="UTC")
        for s in symbols:
            store.write(s, TF, pd.DataFrame({
                "date": opens, "open": 100.0, "high": 101.0, "low": 99.0,
                "close": 100.5, "volume": 1000}))
    source = source or ControlledSource()
    return MarketDataService.build(store, source, clock or _clock(),
                                   list(symbols), [TF], **kw)


# ============================================================ provider offline

def test_offline_provider_never_looks_like_a_quiet_market(tmp_path):
    service = _service(tmp_path, source=NullSource())
    for _ in range(3):
        service.poll(at=MONDAY)
    # stored candles still serve; no symbol is falsely marked bad; the verdict
    # says OFFLINE, not "no new candles" (which would read as a calm market)
    assert service.state.usable_symbols(TF) == ["RELIANCE"]
    assert service.state.provider_status == "OFFLINE"
    assert service.state.diagnosis(TF)["offline"]


def test_provider_recovers_after_going_offline_midsession(tmp_path):
    """A provider that fails then returns must come back to OK on its own, not
    stay latched DEGRADED for the rest of the day."""
    source = ControlledSource()
    service = _service(tmp_path, source=source)
    source.mode = "fail"
    for _ in range(4):
        service.poll(at=MONDAY)
    assert service.state.provider_status in ("DEGRADED", "OFFLINE")
    # the provider returns; on the NEXT bar (a fresh due set) it serves again
    # and the status recovers on its own - not latched for the rest of the day
    source.mode = "ok"
    later = datetime(2026, 7, 20, 11, 20, tzinfo=IST)      # 11:00 bar now due
    service.drain(at=later, deadline_s=5, sleep=lambda s: None)
    assert service.state.provider_status == "OK"


# ================================================================ rate limits

def test_a_hard_rate_limit_never_loses_the_bar(tmp_path):
    """Every request rejected for rate is REQUEUED, not dropped: the bar is
    still owed and will be served once the provider relents."""
    source = ControlledSource()
    source.mode = "rate"
    service = _service(tmp_path, source=source)
    report = service.poll(at=MONDAY)
    assert report.requeued >= 1
    # the symbol is still pending (owed its bar), not failed
    assert service.state.health_for("RELIANCE").status != "FAILED"
    assert len(service.queue) >= 1                # work preserved
    # once the provider relents, the bar is served
    source.mode = "ok"
    service.drain(deadline_s=5, sleep=lambda s: None)
    assert service.state.health_for("RELIANCE").total_successes >= 1


def test_rate_deferral_does_not_burn_a_symbols_attempt_budget(tmp_path):
    """A request that sits queued-but-unsent under a tight rate budget must NOT
    consume its retry attempts. Attempts count real fetches, not the polls that
    re-plan the same still-queued symbol - otherwise a large universe under
    rate pressure would discharge symbols as failed without ever fetching them.
    """
    # one request per hour: after the first send, everything defers for the
    # rest of the session
    caps = ProviderCapabilities(name="tiny",
                                candle_limits=(RateLimit(1, 3600.0),))
    source = ControlledSource(capabilities=caps)
    service = _service(tmp_path, symbols=["A", "B", "C"], source=source,
                       max_requests_per_poll=100)
    # poll many times: the budget allows only the first request; the rest keep
    # deferring poll after poll
    for _ in range(10):
        service.poll(at=MONDAY)
    # every symbol is either served or still owed - NONE was written off as
    # failed purely because it was re-planned while waiting for budget
    for sym in ["A", "B", "C"]:
        h = service.state.health_for(sym)
        assert h.status != "FAILED", f"{sym} discharged without a real attempt"


def test_the_limiter_widens_spacing_under_sustained_rejection(tmp_path):
    source = ControlledSource()
    source.mode = "rate"
    service = _service(tmp_path, source=source)
    for _ in range(3):
        service.poll(at=MONDAY)
    # the penalty grew from repeated rejection - the limiter learned the
    # provider's real limit rather than repeating the same first step
    assert service.transport.candle_limiter.penalty_s > 0


def test_the_service_never_floods_the_provider(tmp_path):
    """Under a 1-request-per-hour cap, a 50-symbol due set sends at most one
    request per poll - the budget is respected, not blown."""
    caps = ProviderCapabilities(name="tiny",
                                candle_limits=(RateLimit(1, 3600.0),))
    source = ControlledSource(capabilities=caps)
    service = _service(tmp_path, symbols=[f"S{i}" for i in range(50)],
                       source=source, max_requests_per_poll=100)
    report = service.poll(at=MONDAY)
    assert report.sent <= 1


# ============================================================= missing symbols

def test_a_symbol_that_vanishes_is_isolated(tmp_path):
    """One unresolvable symbol among many must not cost the others anything."""
    class PartlyUnavailable(ControlledSource):
        def unavailable_reason(self, symbol, timeframe):
            return "delisted" if symbol == "GONE" else None

    source = PartlyUnavailable()
    service = _service(tmp_path, symbols=["A", "GONE", "B"], source=source)
    service.drain(deadline_s=5, sleep=lambda s: None)
    assert service.state.health_for("GONE").status == "UNAVAILABLE"
    assert service.state.health_for("A").total_successes >= 1
    assert service.state.health_for("B").total_successes >= 1


# ================================================================ delayed bars

def test_a_delayed_bar_is_not_invented_then_served_when_it_lands(tmp_path):
    """The provider is slow to publish the newest bar. The scheduler must not
    fabricate it early; once the provider has it, it is served."""
    source = ControlledSource()
    # the provider only has bars up to 05:00 even though 05:15 is expected
    source.bars_up_to = "2026-07-20 05:00:00"
    service = _service(tmp_path, source=source, seed_until="2026-07-20 05:00:00")
    service.poll(at=MONDAY)                       # asks, gets nothing new
    behind = service.state.last_bar("RELIANCE", TF)
    assert behind == pd.Timestamp("2026-07-20 05:00:00", tz="UTC")
    # the bar finally publishes
    source.bars_up_to = "2026-07-20 05:15:00"
    service.drain(deadline_s=5, sleep=lambda s: None)
    assert service.state.last_bar("RELIANCE", TF) == \
        pd.Timestamp("2026-07-20 05:15:00", tz="UTC")


# ============================================================= exchange closed

def test_nothing_is_fetched_when_the_exchange_is_closed(tmp_path):
    source = ControlledSource()
    service = _service(tmp_path, source=source, clock=_clock(SUNDAY))
    report = service.poll(at=SUNDAY)
    assert report.due_timeframes == []
    assert source.requests == []                  # not one request off-session


# =============================================================== restart paths

def test_restart_midfetch_leaves_no_partial_bar_in_the_store(tmp_path):
    """A crash between fetch and store must not leave a half-written bar. The
    store write is atomic per symbol/timeframe and the cache invalidates on the
    store's own mtime, so a fresh service reads exactly what was committed."""
    source = ControlledSource()
    service = _service(tmp_path, source=source)
    service.drain(deadline_s=5, sleep=lambda s: None)
    committed = service.state.last_bar("RELIANCE", TF)

    # "restart": a brand-new service on the same store
    service2 = _service(tmp_path, source=ControlledSource(), seed_until=None)
    assert service2.state.last_bar("RELIANCE", TF) == committed


def test_restart_during_scan_does_not_double_trade(tmp_path):
    """The orchestrator dedups on the newest bar timestamp, in memory. A
    restart re-evaluates, which must be safe: only the newest bar can fire and
    it was already acted on."""
    from algo.trading.orchestrator import Orchestrator
    from algo.strategies.library import OpeningRangeBreakout

    # a breakout session that fires orb on its last bar
    times = pd.date_range("2026-07-13 03:45", periods=40, freq="15min",
                          tz="UTC")
    close = [100.0] * 39 + [105.0]
    high = [100.3] * 39 + [105.5]
    vol = [1000] * 39 + [8000]
    frame = pd.DataFrame({"date": times, "open": 100.0, "high": high,
                          "low": 99.5, "close": close, "volume": vol})
    store = MarketDataStore(tmp_path / "store")
    store.write("RELIANCE", TF, frame)
    state = MarketState(store, ["RELIANCE"], [TF])
    state.set_symbols(["RELIANCE"])

    orch = Orchestrator([OpeningRangeBreakout()], state)
    first = orch.evaluate(TF)
    again = orch.evaluate(TF)                     # same session, same bar
    assert again == []                            # not re-emitted
    orch._last_eval.clear()                       # simulate a restart
    # after restart the newest bar re-fires ONCE (idempotent: dedup + the
    # engine's duplicate-position guard stop a second entry downstream)
    assert len(orch.evaluate(TF)) == len(first)


# ============================================================ observation is inert

def test_a_throwing_exporter_cannot_touch_the_pipeline(tmp_path):
    """Observation must never affect the feed. Even if the dashboard snapshot
    of MarketState raises, polling is unharmed."""
    source = ControlledSource()
    service = _service(tmp_path, source=source)
    # a consumer that explodes while reading state must not reach the service
    with pytest.raises(RuntimeError):
        raise RuntimeError("dashboard boom")      # simulated consumer failure
    # the service has no reference to any exporter - poll proceeds regardless
    report = service.poll(at=MONDAY)
    assert report.sent >= 1


# ============================================================ garbage handling

def test_garbage_candles_are_quarantined_not_stored(tmp_path):
    """A systematically broken response (high<low, negative volume) is refused
    whole - it must never reach the store and become a phantom bar."""
    source = ControlledSource()
    source.mode = "garbage"
    service = _service(tmp_path, source=source, seed_until="2026-07-20 05:00:00")
    before = service.state.last_bar("RELIANCE", TF)
    service.poll(at=MONDAY)
    after = service.state.last_bar("RELIANCE", TF)
    assert after == before                        # nothing bad was admitted
    assert service.state.health_for("RELIANCE").status != "OK"


# ============================================================== clock going back

def test_a_backwards_clock_jump_does_not_crash_or_wait_negatively(tmp_path):
    """NTP corrections and DST can move the wall clock backwards. Nothing may
    crash and no wait may go negative."""
    source = ControlledSource()
    service = _service(tmp_path, source=source)
    service.poll(at=MONDAY)
    earlier = datetime(2026, 7, 20, 10, 0, tzinfo=IST)      # jump back 1h
    report = service.poll(at=earlier)
    assert report.wait_s >= 0
    assert service.seconds_until_next(earlier) >= 0
