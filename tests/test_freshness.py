"""Market-data freshness (§1, §12) and the STALE surface.

The first paper session displayed prices from a bar that was hours old while
reporting the data as current, because the only age in the system was the
snapshot's WRITE time and the UI compared that to the wall clock. A trace
measured a 4,261-minute-old candle being reported as "0.0s" old.

These tests pin the distinctions that failure could not make:

  * old candles while the market is OPEN  -> STALE, with a reason
  * old candles while the market is SHUT  -> correct, not stale
  * age is measured on the CANDLES, never on the exporter
  * the verdict comes from the engine's clock, not from wall-clock time
"""

from datetime import datetime

import pandas as pd
import pytest

from algo.trading import freshness as fr
from algo.trading.clock import IST, MarketClock
from algo.marketdata import MarketState
from algo.data.store import MarketDataStore

TF = "15m"
# a Monday session; 11:00 is inside 09:15-15:30
OPEN_AT = datetime(2026, 7, 20, 11, 0, tzinfo=IST)
SUNDAY = datetime(2026, 7, 19, 11, 0, tzinfo=IST)


def _bars(store, symbol, last_open_ist, n=5, tf=TF):
    """Write n bars ENDING at ``last_open_ist``."""
    opens = pd.date_range(end=pd.Timestamp(last_open_ist), periods=n,
                          freq="15min", tz="Asia/Kolkata")
    store.write(symbol, tf, pd.DataFrame({
        "date": opens, "open": 100.0, "high": 101.0, "low": 99.0,
        "close": 100.5, "volume": 1000}))


def _feed(tmp_path, symbols=("RELIANCE",), tolerance=1):
    store = MarketDataStore(tmp_path / "store")
    return MarketState(store, list(symbols), [TF],
                          stale_tolerance_bars=tolerance)


def _clock(now):
    clock = MarketClock.build()
    clock.now = lambda: now
    return clock


# ================================================================= verdicts

def test_current_candles_while_open_are_fresh(tmp_path):
    feed = _feed(tmp_path)
    _bars(feed.store, "RELIANCE", "2026-07-20 10:45")     # the bar due at 11:00
    report = feed.freshness(TF, clock=_clock(OPEN_AT))
    assert report.status == fr.FRESH
    assert report.ok and not report.stale


def test_old_candles_while_open_are_stale_with_a_reason(tmp_path):
    """THE regression: market open, data hours old, must not read as current."""
    feed = _feed(tmp_path)
    _bars(feed.store, "RELIANCE", "2026-07-17 15:15")     # the previous Friday
    report = feed.freshness(TF, clock=_clock(OPEN_AT))
    assert report.status == fr.STALE
    assert not report.ok
    assert report.worst_bars_behind > 100
    assert "STALE DATA" in report.headline()
    assert "RELIANCE" in report.headline()
    assert "behind the expected" in report.symbols[0].reason


def test_old_candles_while_closed_are_not_stale(tmp_path):
    """Absence of new bars when none are due is correct, not a fault. Calling
    it stale would cry wolf every evening and every weekend."""
    feed = _feed(tmp_path)
    _bars(feed.store, "RELIANCE", "2026-07-17 15:15")
    report = feed.freshness(TF, clock=_clock(SUNDAY))
    assert report.status == fr.CLOSED
    assert report.ok                                   # not a fault
    assert "market closed" in report.headline()


def test_a_symbol_with_no_bars_is_missing_not_stale(tmp_path):
    feed = _feed(tmp_path, symbols=("RELIANCE", "GHOST"))
    _bars(feed.store, "RELIANCE", "2026-07-20 10:45")
    report = feed.freshness(TF, clock=_clock(OPEN_AT))
    assert [s.symbol for s in report.missing] == ["GHOST"]
    assert report.missing[0].reason == "no candles stored for this symbol"


def test_one_bar_behind_is_tolerated_during_the_fetch_window(tmp_path):
    """A bar closes, then the scheduler waits its grace period before
    fetching. Being one bar behind in that window is normal; flagging it would
    fire at every single bar boundary."""
    feed = _feed(tmp_path, tolerance=1)
    _bars(feed.store, "RELIANCE", "2026-07-20 10:30")   # 1 behind the 10:45 bar
    report = feed.freshness(TF, clock=_clock(OPEN_AT))
    assert report.status == fr.FRESH
    assert report.symbols[0].bars_behind == 1

    strict = _feed(tmp_path, tolerance=0)
    strict.store = feed.store
    assert strict.freshness(TF, clock=_clock(OPEN_AT)).status == fr.STALE


def test_two_bars_behind_is_stale(tmp_path):
    feed = _feed(tmp_path, tolerance=1)
    _bars(feed.store, "RELIANCE", "2026-07-20 10:15")   # 2 behind
    report = feed.freshness(TF, clock=_clock(OPEN_AT))
    assert report.status == fr.STALE
    assert report.symbols[0].bars_behind == 2


# ====================================================== the clock is the clock

def test_the_verdict_uses_the_ENGINE_clock_not_wall_time(tmp_path):
    """The clock is the single definition of market time. Reading wall time
    here would give freshness its own notion of the session - and did: with
    wall time, no injected moment could ever produce a STALE verdict.
    """
    feed = _feed(tmp_path)
    _bars(feed.store, "RELIANCE", "2026-07-17 15:15")
    # same store, same data, two different injected moments -> two verdicts
    assert feed.freshness(TF, clock=_clock(OPEN_AT)).status == fr.STALE
    assert feed.freshness(TF, clock=_clock(SUNDAY)).status == fr.CLOSED


def test_without_a_clock_nothing_is_falsely_called_fresh(tmp_path):
    """No clock means no way to know what is due, so the honest answer is
    'no bar is due' - never a fabricated FRESH."""
    feed = _feed(tmp_path)
    _bars(feed.store, "RELIANCE", "2026-07-17 15:15")
    report = feed.freshness(TF)
    assert report.status == fr.CLOSED
    assert report.expected_bar is None


# ==================================================== price carries its time

def test_marks_return_the_price_WITH_its_bar_time(tmp_path):
    """A price separated from its timestamp is what let hours-old candles be
    shown as live prices."""
    feed = _feed(tmp_path)
    _bars(feed.store, "RELIANCE", "2026-07-20 10:45")
    marks = feed.marks(TF)
    price, bar_time = marks["RELIANCE"]
    assert price == 100.5
    assert isinstance(bar_time, pd.Timestamp) and bar_time.tzinfo is not None
    assert feed.latest_prices(TF)["RELIANCE"] == price      # same number


def test_report_is_json_safe_and_carries_the_12_panel_fields(tmp_path):
    import json
    feed = _feed(tmp_path, symbols=("RELIANCE", "GHOST"))
    _bars(feed.store, "RELIANCE", "2026-07-17 15:15")
    data = feed.freshness(TF, clock=_clock(OPEN_AT)).to_dict()
    json.dumps(data)
    for key in ("status", "total", "fresh", "stale", "missing", "live",
                "worst_bars_behind", "newest_bar", "expected_bar", "headline"):
        assert key in data, key
    assert data["total"] == 2 and data["missing"] == 1


# =============================================== bounded live seed window

def test_live_lookback_is_bounded(tmp_path):
    """A symbol the store has never seen must seed a SMALL window on the live
    path: the ingestion default is 365 days, which would pull a year per
    symbol the first time an uncovered timeframe is scanned.

    Asserted on the REQUEST the scheduler emits, not on a configured number -
    the window that actually goes to the provider is the thing that costs.
    """
    from algo.marketdata import TimeframeScheduler

    state = _feed(tmp_path)                       # store has no bars at all
    scheduler = TimeframeScheduler(_clock(OPEN_AT), [TF], grace_seconds=0)
    assert scheduler.live_lookback_days <= 30

    requests = scheduler.plan(state, at=OPEN_AT)
    assert len(requests) == 1
    span = requests[0].end - requests[0].start
    assert span <= pd.Timedelta(days=scheduler.live_lookback_days + 1), \
        f"seed window is {span}, not the bounded live lookback"
