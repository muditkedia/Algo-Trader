"""STRAT-07 geometry, exits, timed suppression, and scanner tests."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from algo.core.enums import Direction
from algo.strategies.library import ALL_STRATEGIES, OpeningLiquiditySweep
from algo.trading.models import Position
from algo.trading.orchestrator import Orchestrator
from algo.trading.portfolio import PortfolioEngine
from algo.trading.risk import AccountRiskEngine, RiskLimits
from algo.trading.trademanager import TradeManager
from tests.strat07_fixtures import strat07_frames


class _State:
    context_symbols = ["NIFTY50"]

    def __init__(self, frames):
        self.frames = frames

    def usable_symbols(self, timeframe):
        return [s for s in self.frames if s != "NIFTY50"]

    def history(self, symbol, timeframe, bars=None):
        frame = self.frames.get(symbol, pd.DataFrame())
        return frame.tail(bars) if bars else frame


@pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
def test_strat07_emits_bidirectional_opening_sweep(direction):
    frames = strat07_frames(direction.value)
    signal = Orchestrator(
        [OpeningLiquiditySweep()], _State(frames)).evaluate("5m")[0]
    prepared = OpeningLiquiditySweep().prepare(frames["RELIANCE"])
    row = prepared.iloc[-1]
    wick = row["lower_wick_ratio" if direction == Direction.LONG
               else "upper_wick_ratio"]
    assert signal.strategy == "liquidity_sweep_5m"
    assert signal.direction == direction
    assert signal.priority_score == pytest.approx(
        0.50 * wick + 0.50 * row["rvol"])
    assert signal.target2 == pytest.approx(
        row["or_high" if direction == Direction.LONG else "or_low"])
    assert signal.timeout_target == pytest.approx(row["vwap"])
    assert signal.timed_block_group == "liquidity_sweep"
    assert pd.Timestamp(signal.timed_block_until) == signal.bar_time + pd.Timedelta(
        minutes=60)


def test_strat07_rejects_a_deep_sweep():
    frames = strat07_frames("long")
    frame = frames["RELIANCE"].copy()
    frame.loc[len(frame) - 1, "low"] -= 2.0
    frames["RELIANCE"] = frame
    assert Orchestrator(
        [OpeningLiquiditySweep()], _State(frames)).evaluate("5m") == []


def test_strat07_is_registered_without_a_duplicate():
    matches = [c for c in ALL_STRATEGIES if c.meta.spec_id == "STRAT-07"]
    assert matches == [OpeningLiquiditySweep]


def test_sweep_blocks_orb_for_60_minutes_across_restart(tmp_path):
    signal = Orchestrator(
        [OpeningLiquiditySweep()], _State(strat07_frames("long"))).evaluate(
            "5m")[0]
    path = tmp_path / "portfolio.json"
    portfolio = PortfolioEngine(path)
    position = Position(
        position_id="s", symbol=signal.symbol, strategy=signal.strategy,
        timeframe="5m", quantity=10, entry_price=signal.entry_ref,
        entry_ts="now", stop=signal.stop, initial_stop=signal.stop,
        session=signal.session, timed_block_group=signal.timed_block_group,
        timed_block_until=signal.timed_block_until)
    portfolio.add_position(position)
    portfolio.close_position(position, signal.entry_ref + 1, "target")
    portfolio = PortfolioEngine(path)
    assert portfolio.load()
    risk = AccountRiskEngine(RiskLimits(
        deploy_today=500_000, min_trade_allocation=0))
    blocked = replace(
        signal, strategy="orb_5m", timed_block_group="",
        timed_block_until="", blocked_by_timed_groups=("liquidity_sweep",),
        bar_time=signal.bar_time + pd.Timedelta(minutes=30))
    assert not risk.check_entry(blocked, portfolio).allowed
    permitted = replace(blocked,
                        bar_time=signal.bar_time + pd.Timedelta(minutes=61))
    assert risk.check_entry(permitted, portfolio).allowed


def test_sweep_exits_after_six_bars_without_reaching_vwap():
    spec = OpeningLiquiditySweep.execution
    position = Position(
        position_id="t", symbol="RELIANCE", strategy="liquidity_sweep_5m",
        timeframe="5m", quantity=10, entry_price=100, entry_ts="now",
        stop=99, initial_stop=99, timeout_target=101, direction="long")
    manager = TradeManager({"liquidity_sweep_5m": spec})
    decision = None
    for i in range(6):
        decision = manager.manage(position, pd.Series({
            "date": pd.Timestamp("2024-01-01 04:00", tz="UTC")
                    + pd.Timedelta(minutes=5 * i),
            "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.2,
        }), spec=spec)
    assert decision.action == "exit"
    assert decision.reason == "target_timeout"
