"""STRAT-03 geometry, scanner, execution, risk, and suppression tests."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from algo.core.enums import Direction
from algo.strategies.library import ALL_STRATEGIES, OpeningDriveMomentum
from algo.trading.orchestrator import Orchestrator
from algo.trading.portfolio import PortfolioEngine
from algo.trading.models import Position
from algo.trading.risk import AccountRiskEngine, RiskLimits
from tests.strat03_fixtures import strat03_frames


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
def test_strat03_emits_only_the_configured_drive_bar(direction):
    frames = strat03_frames(direction.value)
    signals = Orchestrator(
        [OpeningDriveMomentum()], _State(frames)).evaluate("5m")
    signal = next(s for s in signals if s.symbol == "RELIANCE")
    assert signal.strategy == "opening_drive_5m"
    assert signal.direction == direction
    assert signal.grade_multiplier == 1.0
    assert signal.priority_score == pytest.approx(
        0.60 * 3.0 + 0.40 * (1.30 / 1.40))
    assert signal.stop < signal.entry_ref if direction == Direction.LONG \
        else signal.stop > signal.entry_ref


def test_strat03_rejects_a_large_counter_wick():
    frames = strat03_frames("long")
    frame = frames["RELIANCE"].copy()
    frame.loc[frame.index[-1], "low"] = 99.40
    frames["RELIANCE"] = frame
    assert Orchestrator(
        [OpeningDriveMomentum()], _State(frames)).evaluate("5m") == []


def test_strat03_is_registered_for_live_scanning():
    cls = next(c for c in ALL_STRATEGIES if c.meta.name == "opening_drive_5m")
    assert cls is OpeningDriveMomentum
    assert cls.meta.spec_id == "STRAT-03" and cls.meta.timeframe == "5m"


def test_opening_drive_session_block_survives_a_closed_position(tmp_path):
    portfolio = PortfolioEngine(tmp_path / "portfolio.json")
    position = Position(
        position_id="p", symbol="RELIANCE", strategy="opening_drive_5m",
        timeframe="5m", quantity=10, entry_price=100, entry_ts="now",
        stop=99, initial_stop=99, session="2024-01-29",
        session_block_group="opening_drive")
    portfolio.add_position(position)
    portfolio.close_position(position, 101, "target")
    assert portfolio.session_blocked(
        "RELIANCE", ("opening_drive",), "2024-01-29")

    frames = strat03_frames("long")
    drive = Orchestrator(
        [OpeningDriveMomentum()], _State(frames)).evaluate("5m")[0]
    later_orb = replace(
        drive, strategy="orb_5m", session_block_group="",
        blocked_by_session_groups=("opening_drive",))
    decision = AccountRiskEngine(RiskLimits(
        deploy_today=500_000, min_trade_allocation=0)).check_entry(
            later_orb, portfolio)
    assert not decision.allowed
    assert "session blocker" in decision.reason
