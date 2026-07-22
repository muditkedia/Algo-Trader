"""STRAT-10 geometry, integration, multi-stage exit, and blocker tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from algo.core.costs import FlatCostModel, Product
from algo.core.enums import Direction
from algo.execution import ExecutionSpec, execute_signal
from algo.risk.engine import RiskParams
from algo.strategies.library import (
    ALL_STRATEGIES, GeometricChannelContinuation,
)
from algo.trading.config import RiskLimits
from algo.trading.models import Position
from algo.trading.orchestrator import Orchestrator
from algo.trading.portfolio import PortfolioEngine
from algo.trading.risk import AccountRiskEngine
from algo.trading.recovery import RecoveryManager
from algo.trading.trademanager import TradeManager
from tests.strat10_fixtures import strat10_frames


class _State:
    context_symbols = ["NIFTY50"]

    def __init__(self, frames):
        self.frames = frames

    def usable_symbols(self, timeframe):
        return list(self.frames)

    def history(self, symbol, timeframe, bars=None):
        frame = self.frames[symbol]
        return frame.tail(bars) if bars else frame


@pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
def test_strat10_emits_bidirectional_channel_continuation(direction):
    frames = strat10_frames(direction.value)
    strategy = GeometricChannelContinuation()
    signal = Orchestrator([strategy], _State(frames)).evaluate("5m")[0]
    prepared = strategy.prepare(frames["RELIANCE"])
    strategy.prepare_context({"RELIANCE": prepared},
                             {"NIFTY50": frames["NIFTY50"]})
    row = prepared.iloc[-1]
    assert signal.strategy == "geometric_channel_5m"
    assert signal.direction == direction
    assert signal.priority_score == pytest.approx(
        0.50 * row["channel_r2"]
        + 0.50 * abs(row["channel_slope_pct"]))
    assert row["channel_r2"] >= 0.70
    assert row["channel_width"] >= row["atr"]
    assert signal.spec.dynamic_target2
    assert signal.spec.target2_partial_fraction == 0.25
    assert signal.blocked_by_active_groups == ("primary_trend",)
    assert signal.stop < signal.entry_ref if direction == Direction.LONG \
        else signal.stop > signal.entry_ref


def test_channel_fit_is_prior_only_and_ignores_trigger_close():
    frame = strat10_frames("long")["RELIANCE"]
    strategy = GeometricChannelContinuation()
    before = strategy.prepare(frame).iloc[-1]
    changed = frame.copy()
    changed.loc[changed.index[-1], "close"] += 50.0
    after = strategy.prepare(changed).iloc[-1]
    for column in ("channel_mid", "channel_slope_pct", "channel_r2",
                   "channel_upper", "channel_lower"):
        assert after[column] == pytest.approx(before[column])


def test_strat10_rejects_a_low_quality_nonlinear_channel():
    frames = strat10_frames("long")
    frame = frames["RELIANCE"]
    start = len(frame) - 21
    frame.loc[start:start + 19, "close"] = (
        frame.loc[start:start + 19, "close"].mean()
        + np.resize(np.array([-1.5, 1.5, 0.0]), 20))
    frame.loc[start:start + 19, "high"] = frame.loc[start:start + 19, "close"] + 0.1
    frame.loc[start:start + 19, "low"] = frame.loc[start:start + 19, "close"] - 0.1
    assert Orchestrator(
        [GeometricChannelContinuation()], _State(frames)).evaluate("5m") == []


def test_strat10_is_the_only_specification_registration():
    matches = [c for c in ALL_STRATEGIES if c.meta.spec_id == "STRAT-10"]
    assert matches == [GeometricChannelContinuation]


def test_backtest_books_two_partials_and_preserves_runner():
    bars = pd.DataFrame({
        "date": pd.date_range("2024-03-04 09:15", periods=4,
                              freq="5min", tz="UTC"),
        "open": [100, 101, 106, 101],
        "high": [101, 106, 111, 102],
        "low": [99, 100, 106, 100.5],
        "close": [100, 105, 110, 101],
        "volume": [1000] * 4,
        "stop": [95] * 4, "tp1": [105] * 4,
        "upper": [110] * 4, "symbol": ["TEST"] * 4})
    spec = ExecutionSpec(
        stop_kind="column", stop_col="stop", hard_stop_pct=None,
        target_kind="column", target_col="tp1", partial_fraction=0.50,
        target2_col="upper", target2_partial_fraction=0.25,
        dynamic_target2=True, trail="none", intraday=True,
        allow_overnight=False, max_hold_bars=None)
    trade = execute_signal(
        bars, 0, spec=spec, atr=1.0, swing_low=None,
        cost_model=FlatCostModel(0.0), product=Product.INTRADAY,
        stake=10_000.0, params=RiskParams())
    assert trade.exit_reason == "session_squareoff"
    assert trade.profit_abs == pytest.approx(525.0)


def test_live_manager_persists_second_partial_and_runner(tmp_path):
    spec = ExecutionSpec(
        stop_kind="column", stop_col="stop", hard_stop_pct=None,
        target_kind="column", target_col="tp1", partial_fraction=0.50,
        target2_col="upper", target2_partial_fraction=0.25,
        dynamic_target2=True, trail="none", intraday=True,
        allow_overnight=False, max_hold_bars=None)
    position = Position(
        position_id="p1", symbol="RELIANCE", strategy="gcc", timeframe="5m",
        quantity=100, open_quantity=100, entry_price=100,
        entry_ts="2024-03-04T09:15:00+00:00", stop=95, initial_stop=95,
        target=105, target2=110, partial_fraction=0.5)
    manager = TradeManager({"gcc": spec})
    first = manager.manage(position, pd.Series(
        {"date": pd.Timestamp("2024-03-04 09:20", tz="UTC"),
         "open": 101, "high": 106, "low": 100, "close": 105,
         "upper": 110}), spec=spec)
    assert first.action == "partial" and first.partial_stage == 1
    portfolio = PortfolioEngine(tmp_path / "portfolio.json")
    portfolio.positions[position.position_id] = position
    portfolio.book_partial(position, first.partial_qty, first.price, stage=1)
    position.stop, position.target = first.new_stop, position.target2
    second = manager.manage(position, pd.Series(
        {"date": pd.Timestamp("2024-03-04 09:25", tz="UTC"),
         "open": 106, "high": 112, "low": 106, "close": 111,
         "upper": 110}), spec=spec)
    assert second.action == "partial" and second.partial_stage == 2
    assert second.partial_qty == 25
    portfolio.book_partial(position, second.partial_qty, second.price, stage=2)
    assert position.open_quantity == 25 and position.target2_done
    recovered = PortfolioEngine(tmp_path / "portfolio.json")
    assert recovered.load()
    assert recovered.positions["p1"].target2_done
    assert recovered.positions["p1"].open_quantity == 25


def test_strat10_active_owner_block_is_enforced_then_released(tmp_path):
    frames = strat10_frames("long")
    signal = Orchestrator(
        [GeometricChannelContinuation()], _State(frames)).evaluate("5m")[0]
    portfolio = PortfolioEngine(tmp_path / "portfolio.json")
    owner = Position(
        position_id="vwap", symbol="RELIANCE", strategy="vwap_trend_5m",
        timeframe="5m", quantity=10, entry_price=100,
        entry_ts="2024-03-04T09:15:00+00:00", stop=99, initial_stop=99,
        active_block_group="primary_trend")
    portfolio.positions[owner.position_id] = owner
    risk = AccountRiskEngine(RiskLimits(
        allow_multiple_strategies_per_symbol=True, min_trade_allocation=0.0))
    denied = risk.check_entry(signal, portfolio)
    assert not denied and "active owner blocker" in denied.reason
    owner.status = "CLOSED"
    assert risk.check_entry(signal, portfolio).allowed


def test_recovery_infers_both_partial_stages_from_broker_quantity(tmp_path):
    class _Adapter:
        def positions(self):
            return [{"symbol": "RELIANCE", "quantity": 25,
                     "avg_price": 100.0}]

        def open_orders(self):
            return []

    class _Events:
        def emit(self, *args, **kwargs):
            return None

    portfolio = PortfolioEngine(tmp_path / "portfolio.json")
    position = Position(
        position_id="gcc", symbol="RELIANCE", strategy="gcc", timeframe="5m",
        quantity=100, open_quantity=100, entry_price=100,
        entry_ts="2024-03-04T09:15:00+00:00", stop=95, initial_stop=95,
        target=105, target2=110, partial_fraction=0.50,
        target2_partial_fraction=0.25)
    portfolio.add_position(position)
    report = RecoveryManager(
        portfolio, _Adapter(), _Events()).recover()
    recovered = portfolio.positions["gcc"]
    assert report.partial_fills == ["gcc"]
    assert recovered.partial_done and recovered.target2_done
    assert recovered.open_quantity == 25
    assert recovered.stop == 100
    assert recovered.target is None and recovered.target2 is None
    reloaded = PortfolioEngine(tmp_path / "portfolio.json")
    assert reloaded.load() and reloaded.positions["gcc"].target2_done
