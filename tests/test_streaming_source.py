"""StreamingCandleSource + SmartApiQuoteFeed - the websocket market-data path.

Covers: startup seeding through delegation, steady-state serving with ZERO
historical calls, gap repair bounded to the outage, validation mode, quote
serving from ticks, reconnect bookkeeping, feature-flag capability selection,
and the full MarketDataService integration (scanner-triggering completions
from locally built candles).
"""

from datetime import datetime

import pandas as pd
import pytest

from algo.data.ohlcv import OHLCV_COLUMNS
from algo.data.providers.smartapi.quotefeed import SmartApiQuoteFeed
from algo.data.store import MarketDataStore
from algo.marketdata import (
    LocalCandleEngine, MarketDataService, StreamingCandleSource,
)
from algo.marketdata.source import MarketDataSource
from algo.trading.clock import IST, MarketClock

T0 = pd.Timestamp("2026-07-22 03:45:00", tz="UTC").timestamp()   # 09:15 IST


class FakeFallback(MarketDataSource):
    """Records every delegated request; serves a constant candle per bucket."""

    name = "hist-fake"

    def __init__(self):
        self.calls = []

    def fetch_candles(self, symbol, timeframe, start, end):
        self.calls.append((symbol, timeframe, pd.Timestamp(start),
                           pd.Timestamp(end)))
        step = {"1m": 60, "3m": 180, "5m": 300, "15m": 900,
                "1h": 3600}[timeframe]
        s = int(pd.Timestamp(start).timestamp() // step) * step
        e = int(pd.Timestamp(end).timestamp())
        rows = [[pd.Timestamp(b, unit="s", tz="UTC"),
                 50.0, 51.0, 49.0, 50.5, 500.0]
                for b in range(s, e + 1, step)]
        return pd.DataFrame(rows, columns=list(OHLCV_COLUMNS))


def _feed(symbols=("SBIN",)):
    """A quote feed with a stub instrument master and no real socket."""
    class Instruments:
        def token_for(self, symbol):
            return {"SBIN": "3045", "RELIANCE": "2885"}.get(symbol)

    feed = SmartApiQuoteFeed(session=None, instruments=Instruments(),
                             config=None, client_factory=lambda s, c: None)
    feed._set_universe(symbols)
    return feed


def _source(feed=None, timeframes=("1m", "15m"), validate=False):
    feed = feed or _feed()
    engine = LocalCandleEngine(timeframes=timeframes)
    fallback = FakeFallback()
    source = StreamingCandleSource(feed, engine, fallback, validate=validate)
    return source, engine, fallback, feed


def _tape(engine, symbol="SBIN", start=T0, minutes=4):
    vol = 1000
    for m in range(minutes):
        for k in range(2):
            vol += 10
            engine.on_tick(symbol, start + m * 60 + k * 30,
                           100.0 + m + 0.1 * k, vol)


# ------------------------------------------------------------ candle serving

def test_covered_window_serves_locally_with_zero_historical_calls():
    source, engine, fallback, _ = _source()
    _tape(engine, minutes=4)
    frame = source.fetch_candles(
        "SBIN", "1m",
        pd.Timestamp(T0 + 60, unit="s", tz="UTC"),
        pd.Timestamp(T0 + 3 * 60, unit="s", tz="UTC"))
    assert not frame.empty
    assert fallback.calls == []                 # routine polling is GONE
    assert source.served_local == 1


def test_seed_window_delegates_once_and_merges():
    source, engine, fallback, _ = _source()
    _tape(engine, minutes=4)
    start = pd.Timestamp(T0 - 2 * 3600, unit="s", tz="UTC")   # pre-coverage
    frame = source.fetch_candles(
        "SBIN", "1m", start, pd.Timestamp(T0 + 3 * 60, unit="s", tz="UTC"))
    assert len(fallback.calls) == 1
    sym, tf, dstart, dend = fallback.calls[0]
    assert (sym, tf) == ("SBIN", "1m")
    assert dstart == start                       # gap start honoured
    assert dend < pd.Timestamp(T0 + 3 * 60, unit="s", tz="UTC")
    # merged: historical seed rows AND clean local rows
    assert (frame["open"] == 50.0).any()
    assert (frame["open"] > 99.0).any()
    assert frame["date"].is_monotonic_increasing
    assert not frame["date"].duplicated().any()


def test_gap_repair_delegates_only_the_outage():
    source, engine, fallback, feed = _source()
    _tape(engine, minutes=2)                     # covers m0 (taint) + m1
    feed.outages.append((T0 + 150, T0 + 170))    # outage inside minute 2
    _tape(engine, start=T0 + 3 * 60, minutes=2)  # resumes minute 3 (resync)
    engine.finalize_before(T0 + 10 * 60)
    frame = source.fetch_candles(
        "SBIN", "1m",
        pd.Timestamp(T0 + 60, unit="s", tz="UTC"),
        pd.Timestamp(T0 + 5 * 60, unit="s", tz="UTC"))
    assert len(fallback.calls) == 1
    _, _, dstart, dend = fallback.calls[0]
    assert dstart == pd.Timestamp(T0 + 60, unit="s", tz="UTC")
    assert dend < pd.Timestamp(T0 + 5 * 60, unit="s", tz="UTC")
    assert not frame.empty


def test_unsupported_timeframe_fully_delegates():
    # an engine built WITHOUT 1h (the timeframe exists but was not enabled)
    source, engine, fallback, _ = _source(timeframes=("1m",))
    _tape(engine, minutes=4)
    source.fetch_candles("SBIN", "1h",
                         pd.Timestamp(T0, unit="s", tz="UTC"),
                         pd.Timestamp(T0 + 7200, unit="s", tz="UTC"))
    assert len(fallback.calls) == 1
    assert fallback.calls[0][1] == "1h"


def test_1h_serves_locally_with_session_anchored_buckets():
    """Production 1h candles come from the stream - no historical calls."""
    source, engine, fallback, _ = _source(timeframes=("1m", "1h"))
    vol = 1000
    for h in range(3):                        # 09:20, 10:20, 11:20 ticks
        for k in range(2):
            vol += 10
            engine.on_tick("SBIN", T0 + h * 3600 + 300 + k * 60,
                           100.0 + h, vol)
    frame = source.fetch_candles(
        "SBIN", "1h",
        pd.Timestamp(T0 + 3600, unit="s", tz="UTC"),      # 10:15 bar only
        pd.Timestamp(T0 + 2 * 3600 - 60, unit="s", tz="UTC"))
    assert fallback.calls == []               # zero historical requests
    assert len(frame) == 1
    assert int(frame.iloc[0]["date"].timestamp()) == int(T0 + 3600)  # 10:15


def test_validation_mode_counts_mismatches():
    source, engine, fallback, _ = _source(validate=True)
    _tape(engine, minutes=4)
    source.fetch_candles("SBIN", "1m",
                         pd.Timestamp(T0 + 60, unit="s", tz="UTC"),
                         pd.Timestamp(T0 + 3 * 60, unit="s", tz="UTC"))
    # fallback serves 50.0-candles, local built ~101: guaranteed mismatch
    assert source.validated == 1
    assert source.validation_mismatches == 1
    # validation fetches must not count as gap delegation
    assert source.served_local == 1


# ------------------------------------------------------------------- quotes

def test_quotes_come_from_the_tick_stream():
    source, engine, fallback, feed = _source()
    feed.last_price["SBIN"] = (123.45, T0 + 100)
    quotes = source.fetch_quotes(["SBIN", "RELIANCE"])
    assert quotes["SBIN"].price == pytest.approx(123.45)
    assert "RELIANCE" not in quotes
    assert fallback.calls == []


# ------------------------------------------------------------------ status

def test_status_degrades_on_open_outage_and_silence():
    source, _, _, feed = _source()
    assert source.status() == "OK"               # not started: fallback's OK
    feed.started = True
    feed.last_packet_at = None
    assert source.status() == "OK"               # nothing yet: benign
    feed._down_at = T0
    assert source.status() == "DEGRADED"
    feed._down_at = None
    feed.last_packet_at = 1.0                    # ancient
    assert source.status() == "DEGRADED"


# ------------------------------------------------------- feed normalization

def test_normalize_scales_paise_and_epoch_ms():
    feed = _feed()
    tick = feed.normalize({"token": "3045", "exchange_timestamp":
                           int(T0 * 1000), "last_traded_price": 12345,
                           "volume_trade_for_the_day": 777})
    assert tick == ("SBIN", T0, 123.45, 777)
    assert feed.normalize({"token": "999", "exchange_timestamp": 1,
                           "last_traded_price": 1}) is None
    assert feed.unknown_tokens == 1
    assert feed.normalize({"token": "3045", "exchange_timestamp": 0,
                           "last_traded_price": 100}) is None
    assert feed.bad_packets == 1


def test_feed_records_outage_windows():
    feed = _feed()
    feed.started = True
    feed._handle_close(None)
    assert feed.open_outage_since() is not None
    feed._sws = type("S", (), {"subscribe":
                               staticmethod(lambda *a, **k: None)})()
    feed._handle_open(None)
    assert feed.open_outage_since() is None
    assert len(feed.outages) == 1
    assert feed.connects == 1 and feed.disconnects == 1


def test_dispatcher_receives_normalized_ticks():
    feed = _feed()
    seen = []
    feed.set_dispatcher(lambda *tick: seen.append(tick))
    feed._handle_data(None, {"token": "3045",
                             "exchange_timestamp": int(T0 * 1000),
                             "last_traded_price": 10000,
                             "volume_trade_for_the_day": 5})
    assert seen == [("SBIN", T0, 100.0, 5)]
    assert feed.last_price["SBIN"][0] == 100.0


# ------------------------------------------------- service integration

def test_service_completes_bars_from_local_candles(tmp_path):
    """The full v2 pipeline on the streaming source: a completed 15m bar is
    served from locally built candles, reported complete (the scanner
    trigger), written to the store - with zero historical polling."""
    symbols = ["SBIN"]
    store = MarketDataStore(tmp_path / "store")
    # the store holds yesterday's session, so no seed window is due
    prior = pd.Timestamp("2026-07-22 03:30:00", tz="UTC")
    store.write("SBIN", "15m", pd.DataFrame(
        [[prior, 99.0, 99.5, 98.5, 99.2, 100.0]],
        columns=list(OHLCV_COLUMNS)))

    source, engine, fallback, feed = _source(timeframes=("15m",))
    clock = MarketClock.build()
    # a priming tick BEFORE the bucket makes 09:15 the first CLEAN bucket
    # (coverage + volume baseline established at 09:14)
    engine.on_tick("SBIN", T0 - 60, 99.5, 1000)
    # ticks for the full 09:15 bucket, then one tick after 09:30 (rollover)
    vol = 1000
    for k in range(15):
        vol += 20
        engine.on_tick("SBIN", T0 + k * 60 + 5, 100.0 + k * 0.1, vol)
    engine.on_tick("SBIN", T0 + 900 + 5, 103.0, vol + 20)

    service = MarketDataService.build(store, source, clock, symbols, ["15m"],
                                      grace_seconds=5)
    at = datetime(2026, 7, 22, 9, 30, 10, tzinfo=IST)      # just past close
    completed = []
    for _ in range(4):
        report = service.poll(at=at)
        completed.extend(report.completed)
        if completed:
            break
    assert "15m" in completed                    # the scanner trigger fired
    assert fallback.calls == []                  # zero historical requests
    stored = store.read("SBIN", "15m")
    bucket = pd.Timestamp(T0, unit="s", tz="UTC")
    row = stored[stored["date"] == bucket]
    assert len(row) == 1
    assert row.iloc[0]["close"] == pytest.approx(101.4)
    assert row.iloc[0]["volume"] == pytest.approx(300.0)   # 15 ticks x 20


def test_streaming_capabilities_and_engine_grace_selection():
    from algo.marketdata import STREAMING_CAPABILITIES
    assert STREAMING_CAPABILITIES.supports_streaming
    assert STREAMING_CAPABILITIES.hourly_candle_budget() == 0   # unlimited
    from algo.trading.config import TradingConfig
    assert TradingConfig().market_data_mode == "websocket"      # the default
