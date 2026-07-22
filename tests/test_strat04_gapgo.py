"""STRAT-04 entry, execution, scanner, and suppression integration."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from algo.core.enums import Direction
from algo.strategies.library import ALL_STRATEGIES, GapAndGo
from algo.trading.models import Position
from algo.trading.orchestrator import Orchestrator
from algo.trading.portfolio import PortfolioEngine
from algo.trading.risk import AccountRiskEngine, RiskLimits
from tests.strat04_fixtures import strat04_frames


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
def test_strat04_emits_bidirectional_gap_acceleration(direction):
    frames = strat04_frames(direction.value)
    signals = Orchestrator([GapAndGo()], _State(frames)).evaluate("5m")
    signal = next(s for s in signals if s.symbol == "RELIANCE")
    prepared = GapAndGo().prepare(frames["RELIANCE"])
    row = prepared.iloc[-1]
    assert signal.strategy == "gapgo_5m"
    assert signal.direction == direction
    assert signal.grade_multiplier == 1.0
    assert signal.priority_score == pytest.approx(
        0.50 * abs(row["gap_pct"]) + 0.50 * row["opening_rvol"])
    assert signal.spec.entry == "limit_collar"
    assert signal.stop < signal.entry_ref if direction == Direction.LONG \
        else signal.stop > signal.entry_ref


def test_strat04_rejects_excess_opening_gap_fill():
    frames = strat04_frames("long")
    frame = frames["RELIANCE"].copy()
    previous_close = frame.loc[len(frame) - 3, "close"]
    opening = frame.loc[len(frame) - 2, "open"]
    frame.loc[len(frame) - 2, "low"] = previous_close + 0.5 * (
        opening - previous_close)
    frames["RELIANCE"] = frame
    assert Orchestrator([GapAndGo()], _State(frames)).evaluate("5m") == []


def test_strat04_is_the_only_registered_gapgo():
    gapgo = [c for c in ALL_STRATEGIES if c.meta.spec_id == "STRAT-04"]
    assert gapgo == [GapAndGo]
    assert GapAndGo.meta.name == "gapgo_5m"
    assert not any(c.meta.name == "gapgo_15m" for c in ALL_STRATEGIES)


def test_gapgo_fill_suppresses_orb_after_position_closes(tmp_path):
    frames = strat04_frames("long")
    gap_signal = Orchestrator([GapAndGo()], _State(frames)).evaluate("5m")[0]
    portfolio = PortfolioEngine(tmp_path / "portfolio.json")
    position = Position(
        position_id="p", symbol="RELIANCE", strategy="gapgo_5m",
        timeframe="5m", quantity=10, entry_price=gap_signal.entry_ref,
        entry_ts="now", stop=gap_signal.stop, initial_stop=gap_signal.stop,
        session=gap_signal.session, session_block_group="gap_go")
    portfolio.add_position(position)
    portfolio.close_position(position, gap_signal.entry_ref + 1, "target")
    later_orb = replace(
        gap_signal, strategy="orb_5m", session_block_group="",
        blocked_by_session_groups=("gap_go",))
    decision = AccountRiskEngine(RiskLimits(
        deploy_today=500_000, min_trade_allocation=0)).check_entry(
            later_orb, portfolio)
    assert not decision.allowed
    assert "session blocker" in decision.reason
