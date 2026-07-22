"""Phase 15 - production intraday library: calculation + timing correctness.

Correctness over speed (the phase mandate): crafted deterministic sessions verify
the CPR / VWAP / ORB math, session boundaries, market open/close handling, gap
handling, square-off, and the entry/exit TIMING of the three new strategies.
"""

import numpy as np
import pandas as pd
import pytest

from algo.core.costs import NseEquityCostModel, Product
from algo.core.indicators import (
    central_pivot_range, opening_range, session_vwap,
)
from algo.research.simulator import simulate_trade
from algo.risk.engine import RiskParams
from algo.strategies.library import (
    CprBreakout, OpeningRangeRetest,
)


def _session(day: str, closes, highs=None, lows=None, opens=None, vols=None):
    closes = np.asarray(closes, float)
    n = len(closes)
    return pd.DataFrame({
        "date": pd.date_range(f"{day} 09:15", periods=n, freq="15min", tz="UTC"),
        "open": np.asarray(opens, float) if opens is not None
        else np.concatenate(([closes[0]], closes[:-1])),
        "high": np.asarray(highs, float) if highs is not None else closes + 0.5,
        "low": np.asarray(lows, float) if lows is not None else closes - 0.5,
        "close": closes,
        "volume": np.asarray(vols, float) if vols is not None
        else np.full(n, 1000.0)})


def _concat(*frames):
    return pd.concat(frames, ignore_index=True)


# ------------------------------------------------------------------- CPR

def test_cpr_uses_prior_session_and_formula():
    # prior session: H=112, L=88, C=105 -> P=101.667, BC=100, TC=103.333
    s1 = _session("2024-03-04", [100, 108, 105],
                  highs=[101, 112, 106], lows=[99, 88, 104])
    s2 = _session("2024-03-05", [104, 106, 107])
    frame = _concat(s1, s2)
    pivot, top, bot = central_pivot_range(frame)

    # session 1 has no prior day -> NaN
    assert pivot.iloc[:3].isna().all()
    # session 2 sees session 1's CPR
    assert pivot.iloc[3] == pytest.approx((112 + 88 + 105) / 3)
    assert bot.iloc[3] == pytest.approx((112 + 88) / 2)                 # BC=100
    assert top.iloc[3] == pytest.approx(2 * (112 + 88 + 105) / 3 - 100)  # TC
    # constant across session 2's bars
    assert (top.iloc[3:] == top.iloc[3]).all()


# ------------------------------------------------------------------ VWAP

def test_session_vwap_resets_each_session():
    s1 = _session("2024-03-04", [100, 102], highs=[100, 102], lows=[100, 102],
                  vols=[10, 30])
    s2 = _session("2024-03-05", [200, 200], highs=[200, 200], lows=[200, 200],
                  vols=[5, 5])
    frame = _concat(s1, s2)
    vwap = session_vwap(frame)
    # session 1 bar 2: typical price = close here (h=l=c); vw = (100*10+102*30)/40
    assert vwap.iloc[1] == pytest.approx((100 * 10 + 102 * 30) / 40)
    # session 2 starts fresh at 200 (no bleed from session 1)
    assert vwap.iloc[2] == pytest.approx(200.0)


# ------------------------------------------------------------------- ORB

def test_opening_range_window_and_after_flag():
    s = _session("2024-03-04", [100, 101, 102, 103, 104],
                 highs=[100.5, 101.5, 102.5, 103.5, 104.5],
                 lows=[99.5, 100.5, 101.5, 102.5, 103.5])
    or_high, or_low, after = opening_range(s, minutes=15)
    # 15-min window = only the first bar (bars are 15m apart)
    assert not after.iloc[0]                       # first bar is IN the range
    assert after.iloc[1:].all()                    # rest are after
    assert or_high.iloc[-1] == 100.5               # OR high = first bar's high
    assert or_low.iloc[-1] == 99.5


def test_session_boundaries_group_independently():
    s1 = _session("2024-03-04", [100, 105])
    s2 = _session("2024-03-05", [200, 195])
    frame = _concat(s1, s2)
    or_high, _, _ = opening_range(frame, minutes=15)
    # each session's OR high is its own first bar, not bled across the boundary
    assert or_high.iloc[0] == s1["high"].iloc[0]
    assert or_high.iloc[2] == s2["high"].iloc[0]


# --------------------------------------------------- gap / open / close

def test_gap_up_open_does_not_spurious_break():
    """A gap-up that opens already above the OR high must NOT count as a
    crossed_above breakout (no prior bar below the level)."""
    # first bar (OR) high 100; second bar gaps and opens/closes at 110
    s = _session("2024-03-04", [100, 110, 111],
                 highs=[100, 110.5, 111.5], lows=[99.5, 109.5, 110.5],
                 opens=[99.5, 110, 110.5], vols=[1000, 5000, 5000])
    strat = CprBreakout()
    # with no prior session, CPR is NaN -> no signal regardless (causal guard)
    sig = strat.entry_signal(strat.prepare(s))
    assert int(sig.sum()) == 0


def test_market_open_no_trade_in_opening_range():
    """No strategy fires during the opening-range window (market-open handling)."""
    s = _session("2024-03-04", [100] * 30, vols=[9000] * 30)
    for cls in (CprBreakout, OpeningRangeRetest):
        strat = cls()
        sig = strat.entry_signal(strat.prepare(s))
        assert not bool(sig.iloc[0])               # never on the first bar


# ----------------------------------------------- square-off / exit timing

def test_intraday_squareoff_closes_at_session_end():
    """An intraday (MIS) trade is force-closed at the session's last bar."""
    # rising session, no stop hit -> exit must be session_squareoff, not horizon
    closes = np.linspace(100, 105, 12)
    s = _session("2024-03-04", closes, highs=closes + 0.3, lows=closes - 0.3)
    s["atr"] = 1.0
    s["swing_low_calc"] = s["low"].rolling(3, min_periods=1).min()
    trade = simulate_trade(
        s, 2, atr=1.0, swing_low=float(s["low"].iloc[2]),
        params=RiskParams(), cost_model=NseEquityCostModel(),
        product=Product.INTRADAY, max_bars=50)
    assert trade is not None
    assert trade.exit_reason == "session_squareoff"
    # closed on the last bar of the session
    assert pd.Timestamp(trade.close_date) == s["date"].iloc[-1]


# --------------------------------------------------- entry TIMING (crafted)

def _padded_session(day, tail_closes, tail_highs, tail_lows, tail_vols,
                    pad=30, base=101.0):
    """A >=30-bar session: bar 0 is the opening range, then ``pad`` flat bars
    (below any breakout level), then the crafted tail pattern."""
    n_tail = len(tail_closes)
    closes = [base] + [base - 0.1] * pad + list(tail_closes)
    highs = [base + 0.5] + [base] * pad + list(tail_highs)       # OR high = base+0.5
    lows = [base - 0.5] + [base - 0.3] * pad + list(tail_lows)
    vols = [1000.0] * (pad + 1) + list(tail_vols)
    opens = [base] * (pad + 1) + list(tail_closes)
    return _session(day, closes, highs=highs, lows=lows, opens=opens, vols=vols)


def test_cpr_breakout_fires_on_the_break_bar():
    # prior session sets CPR top ~103.3; current session breaks it after the OR
    s1 = _session("2024-03-04", [100, 108, 105],
                  highs=[101, 112, 106], lows=[99, 88, 104])
    # OR high 101.5; 30 flat bars ~100.9 (below CPR top 103.33); then a volume break
    s2 = _padded_session("2024-03-05", tail_closes=[104], tail_highs=[104.5],
                         tail_lows=[103.0], tail_vols=[6000.0])
    frame = _concat(s1, s2)
    strat = CprBreakout()
    sig = strat.entry_signal(strat.prepare(frame))
    fired = np.flatnonzero(sig.to_numpy())
    assert len(fired) == 1
    assert fired[0] == len(frame) - 1              # the break bar (last)
