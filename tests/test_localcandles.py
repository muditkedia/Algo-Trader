"""LocalCandleEngine - correctness of the ONE production candle builder."""

import pandas as pd
import pytest

from algo.marketdata.localcandles import LocalCandleEngine

#: 2026-07-22 09:15:00 IST == 03:45:00 UTC
T0 = pd.Timestamp("2026-07-22 03:45:00", tz="UTC").timestamp()


def _fill(engine, symbol="SBIN", start=T0, minutes=3, per_minute=4,
          base_price=100.0, base_vol=1000, qty=10):
    """Deterministic tick tape: ``per_minute`` ticks per minute; each tick
    trades ``qty``. Returns the cumulative volume after the tape."""
    vol = base_vol
    for m in range(minutes):
        for k in range(per_minute):
            ts = start + m * 60 + k * (60 / per_minute)
            price = base_price + m + k * 0.1
            vol += qty
            engine.on_tick(symbol, ts, price, vol)
    return vol


def test_bucket_assignment_is_exchange_time_and_session_aligned():
    engine = LocalCandleEngine()
    _fill(engine, minutes=31)
    engine.finalize_before(T0 + 32 * 60)
    for tf, step in (("1m", 60), ("3m", 180), ("5m", 300), ("15m", 900)):
        frame = engine.frame("SBIN", tf, T0, T0 + 31 * 60)
        # first bucket is tainted (unknown volume baseline) so it never
        # appears; every served bucket lands exactly on a tf boundary FROM
        # 09:15 IST (03:45 UTC), which is aligned for all four timeframes
        assert not frame.empty
        for ts in frame["date"]:
            assert int(ts.timestamp()) % step == 0
        assert frame["date"].min() >= pd.Timestamp(T0 + step, unit="s",
                                                   tz="UTC")


def test_ohlcv_matches_the_tape_exactly():
    engine = LocalCandleEngine(timeframes=("1m",))
    _fill(engine, minutes=3, per_minute=4, qty=10)
    engine.finalize_before(T0 + 4 * 60)
    frame = engine.frame("SBIN", "1m", T0, T0 + 3 * 60)
    # minute 0 is tainted; minutes 1 and 2 are served
    assert len(frame) == 2
    m1 = frame.iloc[0]
    assert m1["open"] == pytest.approx(101.0)
    assert m1["high"] == pytest.approx(101.3)
    assert m1["low"] == pytest.approx(101.0)
    assert m1["close"] == pytest.approx(101.3)
    assert m1["volume"] == pytest.approx(40.0)   # 4 ticks x qty 10


def test_first_bucket_is_tainted_and_never_served():
    engine = LocalCandleEngine(timeframes=("1m",))
    _fill(engine, minutes=2)
    engine.finalize_before(T0 + 3 * 60)
    frame = engine.frame("SBIN", "1m", T0, T0 + 2 * 60)
    assert (frame["date"] == pd.Timestamp(T0, unit="s", tz="UTC")).sum() == 0


def test_completed_candles_are_immutable_and_late_ticks_dropped():
    engine = LocalCandleEngine(timeframes=("1m",))
    _fill(engine, minutes=2)
    engine.finalize_before(T0 + 5 * 60)
    before = engine.frame("SBIN", "1m", T0, T0 + 2 * 60)
    engine.on_tick("SBIN", T0 + 90, 999.0, 99999)     # late, minute 1 final
    after = engine.frame("SBIN", "1m", T0, T0 + 2 * 60)
    assert engine.late_ticks == 1
    pd.testing.assert_frame_equal(before, after)


def test_zero_duplicate_completions():
    engine = LocalCandleEngine(timeframes=("1m",))
    _fill(engine, minutes=3)
    engine.finalize_before(T0 + 10 * 60)
    engine.finalize_before(T0 + 20 * 60)      # idempotent
    assert engine.completions == 3


def test_rollover_completes_older_bucket_without_clock():
    engine = LocalCandleEngine(timeframes=("1m",))
    engine.on_tick("SBIN", T0 + 5, 100.0, 1000)
    engine.on_tick("SBIN", T0 + 65, 101.0, 1010)      # rollover: m0 final
    frame = engine.frame("SBIN", "1m", T0, T0 + 120)
    assert frame.empty                                 # m0 tainted, m1 open
    engine.on_tick("SBIN", T0 + 125, 102.0, 1020)     # m1 final and clean
    frame = engine.frame("SBIN", "1m", T0, T0 + 120)
    assert len(frame) == 1
    assert frame.iloc[0]["volume"] == pytest.approx(10.0)


def test_gap_marks_open_buckets_tainted_and_resyncs_volume():
    engine = LocalCandleEngine(timeframes=("1m",))
    # m0 tainted; m1 completes cleanly on rollover into m2; m2 stays open
    _fill(engine, minutes=3)
    engine.mark_gap()
    # ticks resume mid-minute-3 with a jumped cumulative volume
    engine.on_tick("SBIN", T0 + 3 * 60 + 30, 105.0, 5000)
    engine.on_tick("SBIN", T0 + 4 * 60 + 5, 106.0, 5050)
    engine.finalize_before(T0 + 10 * 60)
    frame = engine.frame("SBIN", "1m", T0, T0 + 5 * 60)
    served = {int(ts.timestamp()) for ts in frame["date"]}
    assert int(T0 + 60) in served                      # pre-gap clean candle
    assert int(T0 + 120) not in served                 # open at gap: tainted
    assert int(T0 + 180) not in served                 # post-gap resync taint
    # the uncovered window names the gap for historical repair
    gap = engine.uncovered_before("SBIN", "1m", T0, T0 + 5 * 60)
    assert gap is not None


def test_uncovered_before_none_when_fully_covered():
    engine = LocalCandleEngine(timeframes=("1m",))
    _fill(engine, minutes=4)
    engine.finalize_before(T0 + 10 * 60)
    # window starting AFTER the tainted first bucket is fully covered
    assert engine.uncovered_before("SBIN", "1m", T0 + 60, T0 + 3 * 60) is None
    # a window reaching before coverage is not
    early = engine.uncovered_before("SBIN", "1m", T0 - 3600, T0 + 3 * 60)
    assert early is not None and early[0] == T0 - 3600


def test_uncovered_before_flags_outage_windows():
    engine = LocalCandleEngine(timeframes=("1m",))
    _fill(engine, minutes=4)
    engine.finalize_before(T0 + 10 * 60)
    outage = (T0 + 100, T0 + 130)                       # inside minute 1..2
    gap = engine.uncovered_before("SBIN", "1m", T0 + 60, T0 + 3 * 60,
                                  outages=[outage])
    assert gap is not None
    assert gap[1] >= T0 + 130


def test_memory_is_bounded():
    engine = LocalCandleEngine(timeframes=("1m",), max_bars=5)
    _fill(engine, minutes=30)
    engine.finalize_before(T0 + 40 * 60)
    with engine._lock:
        assert len(engine._candles[("SBIN", "1m")]) <= 6   # 5 final + open


def test_unsupported_timeframe_is_refused():
    with pytest.raises(ValueError):
        LocalCandleEngine(timeframes=("2h",))


def test_1h_buckets_are_session_anchored():
    """NSE hourly bars open 09:15, 10:15, ... - never wall-clock hours."""
    engine = LocalCandleEngine(timeframes=("1h",))
    vol = 1000
    # ticks across three hourly buckets: 09:20, 10:20, 11:20 IST
    for h in range(3):
        for k in range(2):
            vol += 10
            engine.on_tick("SBIN", T0 + h * 3600 + 300 + k * 60,
                           100.0 + h, vol)
    engine.finalize_before(T0 + 4 * 3600)
    frame = engine.frame("SBIN", "1h", T0 - 3600, T0 + 3 * 3600)
    # first bucket (09:15) tainted; 10:15 served with exact anchor label
    assert len(frame) == 2
    assert int(frame.iloc[0]["date"].timestamp()) == int(T0 + 3600)   # 10:15
    assert frame.iloc[0]["volume"] == pytest.approx(20.0)


def test_1h_final_bucket_closes_at_session_close():
    """The 15:15 bucket ends at the 15:30 close, so it finalizes minutes
    after close instead of at 16:15."""
    engine = LocalCandleEngine(timeframes=("1h",), finalize_grace_s=3.0)
    t_1515 = T0 + 6 * 3600                    # 15:15 IST
    engine.on_tick("SBIN", t_1515 - 3600 + 60, 99.0, 500)   # 14:16 baseline
    engine.on_tick("SBIN", t_1515 + 300, 100.0, 1000)       # 15:20 tick
    close = t_1515 + 15 * 60                  # 15:30 IST
    engine.finalize_before(close + 5)
    frame = engine.frame("SBIN", "1h", t_1515, t_1515 + 3600)
    assert len(frame) == 1
    assert int(frame.iloc[0]["date"].timestamp()) == int(t_1515)


def test_snapshot_reports_activity():
    engine = LocalCandleEngine(timeframes=("1m", "5m"))
    _fill(engine, minutes=6)
    engine.finalize_before(T0 + 10 * 60)
    snap = engine.snapshot()
    assert snap["ticks"] == 24
    assert snap["symbols_covered"] == 1
    assert snap["finalized_by_timeframe"]["1m"] == 6
