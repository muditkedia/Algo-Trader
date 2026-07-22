"""Batch-2 intraday strategies: signal correctness on crafted sessions.

Covers the five new canonical strategies (gapgo, insidebar, supertrend,
cpr_reversal, nr7_intraday), the new shared indicators (supertrend,
floor_pivot_supports, prior_session_ohlc), and the engine's new `column`
trail mode. Deterministic synthetic sessions; causality via gate-off tests.
"""

import numpy as np
import pandas as pd
import pytest

from algo.core.costs import FlatCostModel, Product
from algo.core.indicators import (
    floor_pivot_levels, floor_pivot_supports, prior_session_ohlc, supertrend,
)
from algo.execution import ExecutionSpec, execute_signal
from algo.risk.engine import RiskParams
from algo.strategies.library import (
    CprReversal, GapAndGo, InsideBarBreakout, Nr7Intraday,
    SupertrendContinuation,
)

COSTS = FlatCostModel(0.0)


def _session(day, closes, highs=None, lows=None, opens=None, vols=None,
             start="09:15"):
    closes = np.asarray(closes, float)
    n = len(closes)
    return pd.DataFrame({
        "date": pd.date_range(f"{day} {start}", periods=n, freq="15min",
                              tz="UTC"),
        "open": np.asarray(opens, float) if opens is not None
        else np.concatenate(([closes[0]], closes[:-1])),
        "high": np.asarray(highs, float) if highs is not None else closes + 0.5,
        "low": np.asarray(lows, float) if lows is not None else closes - 0.5,
        "close": closes,
        "volume": np.asarray(vols, float) if vols is not None
        else np.full(n, 1000.0)})


def _concat(*frames):
    return pd.concat(frames, ignore_index=True)


def _flat_day(day, n=25, price=100.0, spread=0.5):
    return _session(day, [price] * n, highs=[price + spread] * n,
                    lows=[price - spread] * n, opens=[price] * n)


def _history(n_days=28, price=100.0, first="2024-01-01"):
    """Enough flat full sessions to warm every indicator."""
    days = pd.bdate_range(first, periods=n_days)
    return _concat(*[_flat_day(d.date(), price=price) for d in days])


# ------------------------------------------------------------- indicators

def test_prior_session_ohlc_maps_previous_day():
    s1 = _session("2024-03-04", [100, 104, 102], highs=[101, 105, 103],
                  lows=[99, 100, 101], opens=[100, 100, 104])
    s2 = _session("2024-03-05", [103, 104, 105])
    o, h, l, c = prior_session_ohlc(_concat(s1, s2))
    assert o.iloc[:3].isna().all()
    assert (o.iloc[3], h.iloc[3], l.iloc[3], c.iloc[3]) == (100, 105, 99, 102)


def test_floor_pivot_supports_formula():
    s1 = _session("2024-03-04", [102], highs=[110], lows=[90], opens=[100])
    s2 = _session("2024-03-05", [101, 102])
    frame = _concat(s1, s2)
    pivot, r1, _ = floor_pivot_levels(frame)
    s1_, s2_ = floor_pivot_supports(frame)
    p = (110 + 90 + 102) / 3
    assert pivot.iloc[1] == pytest.approx(p)
    assert s1_.iloc[1] == pytest.approx(2 * p - 110)
    assert s2_.iloc[1] == pytest.approx(p - 20)
    assert np.isnan(s1_.iloc[0])                     # first session: no prior


def test_supertrend_reference_behaviour():
    """Uptrend: line stays below closes and ratchets; a plunge through the
    line flips the direction and the line jumps above price."""
    up = list(np.linspace(100, 130, 40))
    down = [112.0, 108.0]                            # crash through the line
    frame = _session("2024-03-04", up + down)
    line, direction = supertrend(frame, period=10, multiplier=3.0)
    warm = line.notna()
    assert warm.sum() > 20
    up_part = slice(15, 40)
    assert (direction.iloc[up_part] == 1).all()
    assert (line.iloc[up_part] < frame["close"].iloc[up_part]).all()
    assert line.iloc[15:40].is_monotonic_increasing   # ratchet, never widens
    assert direction.iloc[-1] == -1                   # flipped by the plunge
    assert line.iloc[-1] > frame["close"].iloc[-1]    # band now above price


def test_supertrend_flip_is_deterministic():
    frame = _session("2024-03-04", list(np.linspace(100, 130, 40)) + [112, 108])
    a = supertrend(frame, 10, 3.0)
    b = supertrend(frame, 10, 3.0)
    pd.testing.assert_series_equal(a[0], b[0])
    pd.testing.assert_series_equal(a[1], b[1])


# ---------------------------------------------------- engine: column trail

def test_column_trail_ratchets_and_exits_at_the_line():
    closes = [100, 101, 103, 106, 108, 104, 104]
    lows = [99.5, 100.5, 102, 105, 107, 103.0, 103.5]
    bars = _session("2024-03-04", closes, lows=lows)
    bars["line"] = [98.0, 98.5, 100.0, 103.0, 105.0, 105.5, 105.5]
    spec = ExecutionSpec(stop_kind="column", stop_col="line",
                         hard_stop_pct=None, trail="column", trail_col="line",
                         intraday=True, max_hold_bars=None)
    trade = execute_signal(bars, 1, spec=spec, atr=1.0, swing_low=None,
                           cost_model=COSTS, product=Product.INTRADAY,
                           stake=50_000.0, params=RiskParams())
    # entry at 101 with stop 98.5; the line ratchets to 105 by bar 4; bar 5's
    # low (103) crosses the trailed stop -> exit AT the trailed line
    assert trade.exit_reason == "trailing_stop"
    assert trade.exit_price == pytest.approx(105.0)
    assert trade.trailed is True


# ------------------------------------------------------------- strategies

def test_insidebar_pattern_and_break():
    hist = _history()
    # mother (wide), inside bar, then break of the mother high
    day = _session("2024-02-12", [100.0, 100.2, 101.6, 102.0],
                   highs=[101.0, 100.6, 101.8, 102.2],
                   lows=[99.0, 99.8, 100.9, 101.5],
                   opens=[100.0, 100.1, 100.2, 101.7])
    strat = InsideBarBreakout()
    prepared = strat.prepare(_concat(hist, day))
    sig = strat.entry_signal(prepared)
    assert sig.iloc[-2]                               # close 101.6 > 101.0
    assert prepared["ib_low"].iloc[-2] == pytest.approx(99.8)
    # no inside bar (bar 2 exceeds the mother high) -> no signal
    no_pattern = _session("2024-02-12", [100.0, 101.2, 101.6, 102.0],
                          highs=[101.0, 101.4, 101.8, 102.2],
                          lows=[99.0, 99.8, 100.9, 101.5])
    sig = strat.entry_signal(strat.prepare(_concat(hist, no_pattern)))
    assert not sig.iloc[-3:].any()


def test_insidebar_never_spans_the_session_boundary():
    hist = _history()
    # "mother" is the last bar of one day, "inside" the first of the next
    d1 = _session("2024-02-12", [100.0], highs=[102.0], lows=[98.0])
    d2 = _session("2024-02-13", [100.2, 102.5, 102.6],
                  highs=[100.6, 102.8, 102.9], lows=[99.8, 100.1, 102.2])
    strat = InsideBarBreakout()
    sig = strat.entry_signal(strat.prepare(_concat(hist, d1, d2)))
    assert not sig.any()


def test_supertrend_strategy_signals_on_the_flip_bar():
    hist = _history(n_days=6)
    trend = _session("2024-02-12",
                     list(np.linspace(100, 96, 10))       # establish downtrend
                     + list(np.linspace(103, 112, 15)))   # sharp reversal up
    strat = SupertrendContinuation()
    prepared = strat.prepare(_concat(hist, trend))
    sig = strat.entry_signal(prepared)
    assert sig.sum() >= 1
    flip = np.flatnonzero(sig.to_numpy(bool))
    for i in flip:                                    # every signal is a flip
        assert prepared["st_dir"].iloc[i] == 1
        assert prepared["st_dir"].iloc[i - 1] == -1
        assert prepared["st_line"].iloc[i] < prepared["close"].iloc[i]


def _cpr_history_day(day):
    """A session whose prior-day CPR width is small but NONZERO (close off
    the midpoint), so the trailing width median is a real reference."""
    return _session(day, [100.0, 100.2], highs=[100.5, 100.5],
                    lows=[99.5, 99.5], opens=[100.0, 100.0])


def _wide_then_reversal(width_scale):
    """History of normal days, then a WIDE prior day and an S1 dip-reject."""
    days = pd.bdate_range("2024-01-01", periods=24)
    hist = _concat(*[_cpr_history_day(d.date()) for d in days])
    # prior day: huge range (H=110, L=90, C=102) -> wide CPR if scaled up
    prior = _session("2024-02-05", [95, 102],
                     highs=[95 + width_scale, 110], lows=[90, 94],
                     opens=[95, 95])
    # today: dip tags S1 (= 2*P - H) and closes back above it
    p = (110 + 90 + 102) / 3
    s1 = 2 * p - 110
    today = _session("2024-02-06",
                     [s1 + 1.5, s1 + 0.8, s1 + 2.5],
                     highs=[s1 + 2.0, s1 + 1.2, s1 + 3.0],
                     lows=[s1 + 0.5, s1 - 0.5, s1 + 0.6],   # bar 1 tags S1
                     opens=[s1 + 1.2, s1 + 1.1, s1 + 0.9])
    return _concat(hist, prior, today)


def test_cpr_reversal_needs_the_wide_day_gate():
    strat = CprReversal()
    wide = _wide_then_reversal(width_scale=15)
    sig = strat.entry_signal(strat.prepare(wide))
    assert sig.iloc[-2]                               # the rejection bar
    # narrow-CPR variant of the same tape: gate closed, no signal. The
    # history days carry width ~0.13; a prior day closing near its midpoint
    # gives width ~0.03 - narrower than the median, so the gate is closed.
    narrow_prior = _session("2024-02-05", [99.9, 100.05],
                            highs=[100.4, 100.6], lows=[99.4, 99.6])
    days = pd.bdate_range("2024-01-01", periods=24)
    hist = _concat(*[_cpr_history_day(d.date()) for d in days])
    p2 = (100.6 + 99.4 + 100.05) / 3
    s1_2 = 2 * p2 - 100.6
    today2 = _session("2024-02-06", [s1_2 + 0.3, s1_2 + 0.2, s1_2 + 0.4],
                      highs=[s1_2 + 0.5, s1_2 + 0.4, s1_2 + 0.6],
                      lows=[s1_2 + 0.1, s1_2 - 0.2, s1_2 + 0.1])
    sig = strat.entry_signal(strat.prepare(_concat(hist, narrow_prior,
                                                   today2)))
    assert not sig.iloc[-3:].any()


def test_nr7_intraday_gate_and_break():
    # 7 wide sessions, then a narrow (NR7) one, then the break day
    days = pd.bdate_range("2024-01-01", periods=26)
    wide_days = _concat(*[
        _session(d.date(), [100, 101, 100.5], highs=[103, 103.5, 103],
                 lows=[97, 98, 97.5]) for d in days])
    nr7_day = _session("2024-02-06", [100.2, 100.4, 100.3],
                       highs=[100.6, 100.7, 100.6],
                       lows=[99.9, 100.0, 100.0])      # range 0.8 << 6
    break_day = _session("2024-02-07", [100.5, 100.9, 101.2],
                         highs=[100.6, 101.0, 101.4],
                         lows=[100.2, 100.4, 100.8],
                         opens=[100.4, 100.5, 100.95])
    strat = Nr7Intraday()
    prepared = strat.prepare(_concat(wide_days, nr7_day, break_day))
    sig = strat.entry_signal(prepared)
    assert sig.iloc[-2]                               # close 100.9 > 100.7
    assert prepared["prev_low"].iloc[-2] == pytest.approx(99.9)
    # WITHOUT the NR7 day (another wide day instead): same break, no signal
    wide_instead = _session("2024-02-06", [100.2, 100.4, 100.3],
                            highs=[103.0, 100.7, 100.6],
                            lows=[97.0, 100.0, 100.0])
    sig = strat.entry_signal(strat.prepare(
        _concat(wide_days, wide_instead, break_day)))
    assert not sig.iloc[-3:].any()


def test_nr7_intraday_takes_only_the_first_break_of_the_session():
    """Re-crosses of the narrow day's high must NOT re-enter (verification
    fix: Crabel's day-trade is one breakout per expansion day)."""
    days = pd.bdate_range("2024-01-01", periods=26)
    wide_days = _concat(*[
        _session(d.date(), [100, 101, 100.5], highs=[103, 103.5, 103],
                 lows=[97, 98, 97.5]) for d in days])
    nr7_day = _session("2024-02-06", [100.2, 100.4, 100.3],
                       highs=[100.6, 100.7, 100.6],
                       lows=[99.9, 100.0, 100.0])
    # break day crosses the level (100.7) TWICE: break, fade below, re-break
    break_day = _session("2024-02-07",
                         [100.9, 100.4, 100.9, 101.2],
                         highs=[101.0, 100.8, 101.0, 101.4],
                         lows=[100.3, 100.2, 100.3, 100.8],
                         opens=[100.5, 100.85, 100.35, 100.95])
    strat = Nr7Intraday()
    sig = strat.entry_signal(strat.prepare(_concat(wide_days, nr7_day,
                                                   break_day)))
    assert sig.sum() == 1                         # first break only
    assert sig.iloc[-4]                           # ... and it IS the first


def test_all_five_are_registered_with_owned_intraday_specs():
    for cls in (GapAndGo, InsideBarBreakout, SupertrendContinuation,
                CprReversal, Nr7Intraday):
        spec = cls.execution
        assert spec.intraday and not spec.allow_overnight
        assert spec.max_hold_bars is None
        assert spec.entry == ("limit_collar" if cls is GapAndGo
                              else "signal_close")
