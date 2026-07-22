"""STRAT-06 initial-balance, scanner, execution, and risk tests."""

from __future__ import annotations

import pandas as pd
import pytest

from algo.core.enums import Direction
from algo.strategies.library import ALL_STRATEGIES, InitialBalanceBreakout
from algo.trading.models import Position
from algo.trading.orchestrator import Orchestrator
from algo.trading.portfolio import PortfolioEngine
from algo.trading.risk import AccountRiskEngine, RiskLimits
from tests.strat06_fixtures import strat06_frames


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
def test_strat06_emits_bidirectional_initial_balance_break(direction):
    frames = strat06_frames(direction.value)
    signal = Orchestrator(
        [InitialBalanceBreakout()], _State(frames)).evaluate("5m")[0]
    prepared = InitialBalanceBreakout().prepare(frames["RELIANCE"])
    row = prepared.iloc[-1]
    assert signal.strategy == "initial_balance_5m"
    assert signal.direction == direction
    assert signal.grade_multiplier == 1.0
    assert signal.priority_score == pytest.approx(
        0.50 * row["rvol"] + 0.50 * row["atr"] / row["ib_width"])
    assert signal.spec.stop_kind == "column_atr_cap"
    assert signal.stop < signal.entry_ref if direction == Direction.LONG \
        else signal.stop > signal.entry_ref


def test_strat06_rejects_a_blown_out_initial_balance():
    frames = strat06_frames("long")
    frame = frames["RELIANCE"].copy()
    frame.loc[len(frame) - 7, "high"] += 4.0
    frames["RELIANCE"] = frame
    assert Orchestrator(
        [InitialBalanceBreakout()], _State(frames)).evaluate("5m") == []


def test_strat06_is_registered_without_an_orb_alias():
    matches = [c for c in ALL_STRATEGIES if c.meta.spec_id == "STRAT-06"]
    assert matches == [InitialBalanceBreakout]
    assert InitialBalanceBreakout.meta.name == "initial_balance_5m"


def test_strat06_is_allowed_after_stop_but_not_while_orb_is_open(tmp_path):
    frames = strat06_frames("long")
    signal = Orchestrator(
        [InitialBalanceBreakout()], _State(frames)).evaluate("5m")[0]
    portfolio = PortfolioEngine(tmp_path / "portfolio.json")
    prior = Position(
        position_id="o", symbol=signal.symbol, strategy="orb_5m",
        timeframe="5m", quantity=10, entry_price=signal.entry_ref,
        entry_ts="now", stop=signal.stop, initial_stop=signal.stop,
        session=signal.session)
    portfolio.add_position(prior)
    risk = AccountRiskEngine(RiskLimits(
        deploy_today=500_000, min_trade_allocation=0))
    assert not risk.check_entry(signal, portfolio).allowed
    portfolio.close_position(prior, signal.entry_ref - 1, "stop_loss")
    assert risk.check_entry(signal, portfolio).allowed
