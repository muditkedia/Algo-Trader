"""Strategy-owned execution engine - correctness of the project-reset fixes.

Covers the three confirmed backtest bugs (last-bar overnight carry, generic
8-bar holding cap, optimistic gap fills), the session/square-off semantics,
target/partial execution, parity with the frozen research simulator on clean
paths, and the interface guarantee that every registered strategy owns an
ExecutionSpec.
"""

import numpy as np
import pandas as pd
import pytest

from algo.core.costs import FlatCostModel, Product
from algo.core.enums import HoldingScope
from algo.execution import (
    ExecutionSpec,
    atr_trail_intraday,
    atr_trail_swing,
    execute_signal)
from algo.research.simulator import simulate_trade
from algo.risk.engine import RiskParams
from algo.strategies.library import (
    ALL_STRATEGIES, CprBreakout, FirstPullbackAfterBreakout,
    OpeningRangeBreakout, VwapPullback, VwapTrendContinuation,
)

COSTS = FlatCostModel(0.0)          # zero costs: assert on pure execution math
PARAMS = RiskParams()


def _session(day, closes, highs=None, lows=None, opens=None, start="09:15"):
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
        "volume": np.full(n, 1000.0),
        "symbol": "TEST"})


def _concat(*frames):
    return pd.concat(frames, ignore_index=True)


def _flat(day, n, price=100.0, start="09:15"):
    return _session(day, [price] * n, highs=[price] * n, lows=[price] * n,
                    opens=[price] * n, start=start)


WIDE_STOP = ExecutionSpec(stop_kind="column", stop_col="stop_level",
                          hard_stop_pct=None)


def _with_stop(frame, level):
    out = frame.copy()
    out["stop_level"] = level
    return out


# ------------------------------------------- fix 1: no last-bar entry, ever

def test_signal_on_the_sessions_last_bar_is_not_traded():
    s1 = _flat("2024-03-04", 5)
    s2 = _flat("2024-03-05", 5)
    bars = _with_stop(_concat(s1, s2), 90.0)
    # index 4 is session 1's last bar; the old simulator entered here and rode
    # the overnight gap - the declaration makes it untradeable.
    assert execute_signal(bars, 4, spec=WIDE_STOP, atr=1.0, swing_low=None,
                          cost_model=COSTS, product=Product.INTRADAY,
                          stake=50_000.0, params=PARAMS) is None


def test_no_intraday_trade_ever_crosses_a_session(monkeypatch):
    s1 = _flat("2024-03-04", 5)
    s2 = _flat("2024-03-05", 5)
    bars = _with_stop(_concat(s1, s2), 90.0)
    for i in range(len(bars) - 1):
        trade = execute_signal(bars, i, spec=WIDE_STOP, atr=1.0,
                               swing_low=None, cost_model=COSTS,
                               product=Product.INTRADAY, stake=50_000.0,
                               params=PARAMS)
        if trade is not None:
            assert trade.close_date.normalize() == trade.open_date.normalize()


# ---------------------------------- fix 2: session exit, not the 8-bar cap

def test_intraday_runs_to_the_sessions_actual_last_bar():
    n = 25                                     # a full NSE 15m session
    bars = _with_stop(_flat("2024-03-04", n), 90.0)
    trade = execute_signal(bars, 1, spec=WIDE_STOP, atr=1.0, swing_low=None,
                           cost_model=COSTS, product=Product.INTRADAY,
                           stake=50_000.0, params=PARAMS)
    assert trade.exit_reason == "session_squareoff"
    # exits on bar 24 (the last bar), i.e. 23 bars after entry - the old
    # simulator would have stopped at 8
    assert trade.close_date == bars["date"].iloc[n - 1]
    assert trade.holding_min == pytest.approx((n - 2) * 15.0)


def test_swing_honours_the_strategys_own_horizon():
    bars = _with_stop(_flat("2024-03-04", 30), 90.0)
    spec = atr_trail_swing(max_hold_bars=10)
    trade = execute_signal(bars, 2, spec=spec, atr=1.0, swing_low=95.0,
                           cost_model=COSTS, product=Product.DELIVERY,
                           stake=50_000.0, params=PARAMS)
    assert trade.exit_reason == "horizon_end"
    assert trade.close_date == bars["date"].iloc[12]


# --------------------------------------------- fix 3: honest gap-through fills

def test_stop_gap_through_fills_at_the_open_not_the_stop():
    closes = [100, 100, 92, 92]
    opens = [100, 100, 93, 92]                # bar 2 OPENS below the stop (95)
    highs = [100, 100, 93.5, 92.5]
    lows = [100, 100, 91, 91.5]
    bars = _with_stop(_session("2024-03-04", closes, highs, lows, opens), 95.0)
    trade = execute_signal(bars, 1, spec=WIDE_STOP, atr=1.0, swing_low=None,
                           cost_model=COSTS, product=Product.INTRADAY,
                           stake=50_000.0, params=PARAMS)
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_price == pytest.approx(93.0)      # the open, not 95
    assert trade.gap_fill is True


def test_stop_touched_intrabar_fills_at_the_stop():
    closes = [100, 100, 96, 97]
    opens = [100, 100, 99, 96]
    lows = [100, 100, 94.5, 95.5]             # bar 2 trades through 95
    bars = _with_stop(_session("2024-03-04", closes, highs=None, lows=lows,
                               opens=opens), 95.0)
    trade = execute_signal(bars, 1, spec=WIDE_STOP, atr=1.0, swing_low=None,
                           cost_model=COSTS, product=Product.INTRADAY,
                           stake=50_000.0, params=PARAMS)
    assert trade.exit_price == pytest.approx(95.0)
    assert trade.gap_fill is False


# ------------------------------------------------------- targets & partials

def _target_spec(**kw):
    base = dict(stop_kind="column", stop_col="stop_level", hard_stop_pct=None,
                target_kind="column", target_col="target_level")
    base.update(kw)
    return ExecutionSpec(**base)


def _with_levels(frame, stop, target):
    out = frame.copy()
    out["stop_level"] = stop
    out["target_level"] = target
    return out


def test_target_fills_at_the_level_and_gap_open_fills_at_the_open():
    closes = [100, 100, 104, 105]
    highs = [100, 100, 106, 106]
    bars = _with_levels(_session("2024-03-04", closes, highs=highs), 95, 105)
    trade = execute_signal(bars, 1, spec=_target_spec(), atr=1.0,
                           swing_low=None, cost_model=COSTS,
                           product=Product.INTRADAY, stake=50_000.0,
                           params=PARAMS)
    assert trade.exit_reason == "target"
    assert trade.exit_price == pytest.approx(105.0)

    opens = [100, 100, 107, 107]              # gaps OVER the target
    bars = _with_levels(_session("2024-03-04", closes, highs=highs,
                                 opens=opens), 95, 105)
    trade = execute_signal(bars, 1, spec=_target_spec(), atr=1.0,
                           swing_low=None, cost_model=COSTS,
                           product=Product.INTRADAY, stake=50_000.0,
                           params=PARAMS)
    assert trade.exit_price == pytest.approx(107.0)     # the better open
    assert trade.gap_fill is True


def test_same_bar_stop_and_target_is_a_stop_out():
    closes = [100, 100, 100]
    highs = [100, 100, 108]
    lows = [100, 100, 94]                     # spans stop AND target
    bars = _with_levels(_session("2024-03-04", closes, highs, lows), 95, 105)
    trade = execute_signal(bars, 1, spec=_target_spec(), atr=1.0,
                           swing_low=None, cost_model=COSTS,
                           product=Product.INTRADAY, stake=50_000.0,
                           params=PARAMS)
    assert trade.exit_reason == "stop_loss"


def test_partial_books_half_moves_stop_to_breakeven_and_runs_to_target2():
    closes = [100, 100, 106, 108, 112, 100]
    highs = [100, 100, 106, 108, 112.5, 100]
    lows = [100, 100, 103, 107, 111, 100]
    bars = _session("2024-03-04", closes, highs, lows)
    bars["stop_level"] = 95.0
    bars["t1"] = 105.0
    bars["t2"] = 112.0
    spec = _target_spec(target_col="t1", partial_fraction=0.5,
                        target2_col="t2")
    trade = execute_signal(bars, 1, spec=spec, atr=1.0, swing_low=None,
                           cost_model=COSTS, product=Product.INTRADAY,
                           stake=50_000.0, params=PARAMS)
    assert trade.partial and trade.partial_price == pytest.approx(105.0)
    assert trade.exit_reason == "target"
    assert trade.exit_price == pytest.approx(112.0)
    # gross: half at +5%, half at +12% (zero costs)
    qty = 50_000.0 / 100.0
    expected = 0.5 * qty * 5.0 + 0.5 * qty * 12.0
    assert trade.profit_abs == pytest.approx(expected)


def test_after_partial_the_remainder_stops_at_breakeven():
    closes = [100, 100, 106, 99, 99]
    highs = [100, 100, 106, 99.5, 99.5]
    lows = [100, 100, 103, 98, 98.5]
    bars = _session("2024-03-04", closes, highs, lows)
    bars["stop_level"] = 95.0
    bars["t1"] = 105.0
    spec = _target_spec(target_col="t1", partial_fraction=0.5)
    trade = execute_signal(bars, 1, spec=spec, atr=1.0, swing_low=None,
                           cost_model=COSTS, product=Product.INTRADAY,
                           stake=50_000.0, params=PARAMS)
    assert trade.partial
    assert trade.exit_reason == "breakeven_stop"
    assert trade.exit_price == pytest.approx(100.0)     # entry, not 95
    qty = 50_000.0 / 100.0
    assert trade.profit_abs == pytest.approx(0.5 * qty * 5.0)


# ------------------------------------- parity with the frozen simulator

def _parity_frame():
    closes = [100, 101, 102, 101.5, 103, 104, 103.5, 105, 106, 105.5,
              107, 108, 107.5, 109, 110, 109.5, 111, 112, 111.5, 113,
              114, 113.5, 115, 116, 115.5]
    s = _session("2024-03-04", closes)
    s["atr"] = 1.0
    return s


def test_chandelier_intraday_matches_the_frozen_simulator_on_clean_paths():
    """atr_trail_intraday reproduces simulate_trade bit-for-bit when none of
    the fixed bugs (last-bar entry, 8-bar cap, gap-through) is in play."""
    bars = _parity_frame()
    old = simulate_trade(bars, 1, atr=1.0, swing_low=99.0, params=PARAMS,
                         cost_model=COSTS, product=Product.INTRADAY,
                         stake=50_000.0, max_bars=None)
    new = execute_signal(bars, 1, spec=atr_trail_intraday(), atr=1.0,
                         swing_low=99.0, cost_model=COSTS,
                         product=Product.INTRADAY, stake=50_000.0,
                         params=PARAMS)
    assert new.exit_reason == old.exit_reason
    assert new.exit_price == pytest.approx(old.exit_price)
    assert new.profit_abs == pytest.approx(old.profit_abs)
    assert new.close_date == old.close_date


def test_swing_matches_the_frozen_simulator_on_clean_paths():
    bars = _parity_frame()
    old = simulate_trade(bars, 1, atr=1.0, swing_low=99.0, params=PARAMS,
                         cost_model=COSTS, product=Product.DELIVERY,
                         stake=50_000.0, max_bars=10)
    new = execute_signal(bars, 1, spec=atr_trail_swing(max_hold_bars=10),
                         atr=1.0, swing_low=99.0, cost_model=COSTS,
                         product=Product.DELIVERY, stake=50_000.0,
                         params=PARAMS)
    assert new.exit_reason == old.exit_reason
    assert new.exit_price == pytest.approx(old.exit_price)
    assert new.profit_abs == pytest.approx(old.profit_abs)


# --------------------------------------------- the interface guarantee

def test_every_registered_strategy_owns_an_execution_spec():
    for cls in ALL_STRATEGIES:
        spec = cls.execution
        assert isinstance(spec, ExecutionSpec), cls.meta.name
        intraday = cls.meta.holding_scope == HoldingScope.INTRADAY
        assert spec.intraday == intraday, cls.meta.name
        if intraday:
            assert not spec.allow_overnight, cls.meta.name
            assert spec.max_hold_bars is None, cls.meta.name
        else:
            assert spec.max_hold_bars == cls.meta.max_hold_bars, cls.meta.name


def test_structural_strategies_prepare_their_declared_levels():
    """Each published-form strategy's prepare() provides the level columns its
    own ExecutionSpec references."""
    day1 = _session("2024-03-04", np.linspace(100, 104, 25).round(2))
    day2 = _session("2024-03-05", np.linspace(104, 110, 25).round(2))
    frame = _concat(day1, day2)
    for cls in (OpeningRangeBreakout, VwapTrendContinuation, VwapPullback,
                CprBreakout, FirstPullbackAfterBreakout):
        spec = cls.execution
        prepared = cls().prepare(frame)
        for col in filter(None, (spec.stop_col, spec.target_col,
                                 spec.target2_col)):
            assert col in prepared.columns, (cls.meta.name, col)


def test_orb_target_is_one_range_above_the_or_high():
    bars = _session("2024-03-04", np.linspace(100, 104, 25).round(2))
    prepared = OpeningRangeBreakout().prepare(bars)
    after = prepared[prepared["after_range"]]
    expected = after["or_high"] + (after["or_high"] - after["or_low"])
    assert np.allclose(after["or_target"], expected)
