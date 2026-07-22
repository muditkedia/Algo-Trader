"""STRAT-05 state, geometry, scanner, and execution tests."""

from __future__ import annotations

import pandas as pd
import pytest

from algo.core.enums import Direction
from algo.strategies.library import ALL_STRATEGIES, GapFillFailure
from algo.trading.models import Position
from algo.trading.orchestrator import Orchestrator
from algo.trading.portfolio import PortfolioEngine
from algo.trading.risk import AccountRiskEngine, RiskLimits
from tests.strat05_fixtures import strat05_frames


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
def test_strat05_emits_bidirectional_failed_gap_fill(direction):
    frames = strat05_frames(direction.value)
    signal = Orchestrator([GapFillFailure()], _State(frames)).evaluate("5m")[0]
    prepared = GapFillFailure().prepare(frames["RELIANCE"])
    row = prepared.iloc[-1]
    penetration = row["penetration_long" if direction == Direction.LONG
                      else "penetration_short"]
    assert signal.strategy == "gap_fill_failure_5m"
    assert signal.direction == direction
    assert signal.grade_multiplier == 1.0
    assert signal.priority_score == pytest.approx(
        0.50 * row["rvol"] + 0.50 / penetration)
    assert signal.stop < signal.entry_ref if direction == Direction.LONG \
        else signal.stop > signal.entry_ref
    assert signal.spec.no_progress_bars == 8


def test_strat05_rejects_a_completed_gap_fill():
    frames = strat05_frames("long")
    frame = frames["RELIANCE"].copy()
    prior_close = frame.loc[len(frame) - 5, "close"]
    frame.loc[len(frame) - 3:, "low"] = prior_close
    frames["RELIANCE"] = frame
    assert Orchestrator(
        [GapFillFailure()], _State(frames)).evaluate("5m") == []


def test_strat05_is_registered_without_a_duplicate():
    matches = [c for c in ALL_STRATEGIES if c.meta.spec_id == "STRAT-05"]
    assert matches == [GapFillFailure]
    assert GapFillFailure.meta.timeframe == "5m"


def test_strat05_can_follow_a_stopped_gapgo(tmp_path):
    assert GapFillFailure.meta.blocked_by_session_groups == ()
    assert GapFillFailure.meta.exclusive_group == ""
    frames = strat05_frames("long")
    signal = Orchestrator([GapFillFailure()], _State(frames)).evaluate("5m")[0]
    portfolio = PortfolioEngine(tmp_path / "portfolio.json")
    prior = Position(
        position_id="g", symbol=signal.symbol, strategy="gapgo_5m",
        timeframe="5m", quantity=10, entry_price=signal.entry_ref,
        entry_ts="now", stop=signal.stop, initial_stop=signal.stop,
        session=signal.session, session_block_group="gap_go")
    portfolio.add_position(prior)
    portfolio.close_position(prior, signal.entry_ref - 1, "stop_loss")
    decision = AccountRiskEngine(RiskLimits(
        deploy_today=500_000, min_trade_allocation=0)).check_entry(
            signal, portfolio)
    assert decision.allowed
