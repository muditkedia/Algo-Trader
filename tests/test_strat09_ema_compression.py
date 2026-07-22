"""STRAT-09 signal, integration, execution, and interaction tests."""

from __future__ import annotations

import pandas as pd
import pytest

from algo.core.enums import Direction
from algo.strategies.library import (
    ALL_STRATEGIES, EMACompressionBreakout, VwapTrendContinuation,
)
from algo.trading.config import RiskLimits
from algo.trading.models import Order, OrderStatus, Position
from algo.trading.orchestrator import Orchestrator
from algo.trading.portfolio import PortfolioEngine
from algo.trading.risk import AccountRiskEngine
from tests.strat08_fixtures import strat08_frame
from tests.strat09_fixtures import strat09_frames


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
def test_strat09_emits_bidirectional_compression_breakout(direction):
    frames = strat09_frames(direction.value)
    strategy = EMACompressionBreakout()
    signal = Orchestrator([strategy], _State(frames)).evaluate("5m")[0]
    prepared = strategy.prepare(frames["RELIANCE"])
    strategy.prepare_context({"RELIANCE": prepared},
                             {"NIFTY50": frames["NIFTY50"]})
    row = prepared.iloc[-1]
    assert signal.strategy == "ema_compression_5m"
    assert signal.direction == direction
    assert signal.grade_multiplier == 1.0
    assert signal.priority_score == pytest.approx(
        0.50 * row["rvol"] + 0.50 / row["ema_spread"])
    assert bool(row["compression_ready"])
    assert bool(row["bbw_at_low"])
    assert row["adx14"] > prepared["adx14"].iloc[-2]
    assert signal.spec.entry == "limit_collar"
    assert signal.pre_partial_block_group == "ema_compression"
    assert signal.stop < signal.entry_ref if direction == Direction.LONG \
        else signal.stop > signal.entry_ref


def test_strat09_requires_four_prior_compressed_bars():
    frames = strat09_frames("long")
    frame = frames["RELIANCE"]
    trigger = frame.index[-1]
    frame.loc[trigger - 2, "close"] += 3.0
    frame.loc[trigger - 2, "high"] = frame.loc[trigger - 2, "close"] + 0.1
    assert Orchestrator(
        [EMACompressionBreakout()], _State(frames)).evaluate("5m") == []


def test_strat09_is_the_only_specification_registration():
    matches = [c for c in ALL_STRATEGIES if c.meta.spec_id == "STRAT-09"]
    assert matches == [EMACompressionBreakout]


def test_pre_tp1_block_releases_after_partial_and_survives_order_state(tmp_path):
    portfolio = PortfolioEngine(tmp_path / "portfolio.json")
    order = Order(
        client_order_id="emacb-entry", symbol="RELIANCE", side="BUY",
        quantity=10, order_type="LIMIT", intent="entry",
        status=OrderStatus.OPEN, pre_partial_block_group="ema_compression")
    portfolio.orders[order.client_order_id] = order
    assert portfolio.pre_partial_blocked("RELIANCE", ("ema_compression",))
    order.status = OrderStatus.FILLED
    position = Position(
        position_id="p1", symbol="RELIANCE", strategy="ema_compression_5m",
        timeframe="5m", quantity=10, entry_price=100,
        entry_ts=str(pd.Timestamp("2024-02-01", tz="UTC")), stop=99,
        initial_stop=99, pre_partial_block_group="ema_compression")
    portfolio.positions[position.position_id] = position
    assert portfolio.pre_partial_blocked("RELIANCE", ("ema_compression",))
    portfolio.persist()
    recovered = PortfolioEngine(tmp_path / "portfolio.json")
    assert recovered.load()
    assert recovered.pre_partial_blocked("RELIANCE", ("ema_compression",))
    recovered.positions[position.position_id].partial_done = True
    assert not recovered.pre_partial_blocked(
        "RELIANCE", ("ema_compression",))


def test_pre_tp1_block_is_enforced_by_account_risk(tmp_path):
    vwap_frame = strat08_frame("long")
    state = _State({"RELIANCE": vwap_frame})
    state.context_symbols = []
    secondary = Orchestrator(
        [VwapTrendContinuation()], state).evaluate("5m")[0]
    portfolio = PortfolioEngine(tmp_path / "portfolio.json")
    position = Position(
        position_id="p1", symbol="RELIANCE", strategy="ema_compression_5m",
        timeframe="5m", quantity=10, entry_price=100,
        entry_ts=str(pd.Timestamp("2024-02-01", tz="UTC")), stop=99,
        initial_stop=99, pre_partial_block_group="ema_compression")
    portfolio.positions[position.position_id] = position
    risk = AccountRiskEngine(RiskLimits(
        allow_multiple_strategies_per_symbol=True, min_trade_allocation=0.0))
    denied = risk.check_entry(secondary, portfolio)
    assert not denied and "pre-TP1 blocker" in denied.reason
    position.partial_done = True
    assert risk.check_entry(secondary, portfolio).allowed
