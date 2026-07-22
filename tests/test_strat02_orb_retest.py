"""STRAT-02 canonical strategy, state, execution, and portfolio integration."""

from __future__ import annotations

import pandas as pd
import pytest

from algo.core.costs import FlatCostModel, Product
from algo.core.enums import Direction
from algo.execution import execute_signal
from algo.strategies.library import ALL_STRATEGIES, OpeningRangeRetest
from algo.trading.orchestrator import Orchestrator
from algo.trading.portfolio import PortfolioEngine
from algo.trading.models import Position
from tests.strat02_fixtures import strat02_frames


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
def test_strat02_emits_directional_retest_signal(direction):
    frames = strat02_frames(direction.value)
    signals = Orchestrator([OpeningRangeRetest()], _State(frames)).evaluate("5m")
    signal = next(s for s in signals if s.symbol == "RELIANCE")
    assert signal.strategy == "orb_retest_5m"
    assert signal.direction == direction
    assert signal.limit_price is not None
    assert signal.stop < signal.entry_ref if direction == Direction.LONG \
        else signal.stop > signal.entry_ref
    assert signal.priority_score == pytest.approx(
        0.65 * signal.confidence + 0.35 * signal.regime_score)


def test_strat02_is_the_only_registered_orb_retest():
    names = {cls.meta.name for cls in ALL_STRATEGIES}
    assert "orb_retest_5m" in names
    assert "first_pullback_15m" not in names


def test_strat02_state_is_causal_and_requires_retest_before_trigger():
    frames = strat02_frames("long")
    strategy = OpeningRangeRetest()
    prepared = strategy.prepare(frames["RELIANCE"])
    assert not prepared["trigger_base_long"].iloc[-2]
    assert prepared["retest_touched_long"].iloc[-2]
    assert prepared["trigger_base_long"].iloc[-1]
    prefix = strategy.prepare(frames["RELIANCE"].iloc[:-1])
    pd.testing.assert_series_equal(
        prepared["wave_extension_long"].iloc[:-1].reset_index(drop=True),
        prefix["wave_extension_long"].reset_index(drop=True),
        check_names=False)


def test_strat02_directional_execution_uses_dynamic_grade_target():
    bars = pd.DataFrame({
        "date": pd.date_range("2024-01-01 03:45", periods=4,
                              freq="5min", tz="UTC"),
        "open": [100, 100, 100, 101], "high": [100, 100, 101.6, 101],
        "low": [100, 100, 99.8, 100.8], "close": [100, 100, 101.5, 101],
        "retest_stop_long": [99, 99, 99, 99],
        "retest_stop_short": [101, 101, 101, 101],
        "target_r_long": [1.0, 1.0, 1.0, 1.0],
        "target_r_short": [1.0, 1.0, 1.0, 1.0],
        "atr": [1, 1, 1, 1], "invalidate_long": False,
        "invalidate_short": False})
    trade = execute_signal(
        bars, 1, spec=OpeningRangeRetest.execution, atr=1.0,
        swing_low=None, cost_model=FlatCostModel(0.0),
        product=Product.INTRADAY, stake=50_000, direction=Direction.LONG)
    assert trade is not None and trade.partial
    assert trade.partial_price == pytest.approx(101.0)


def test_strat02_active_conflict_allows_entry_after_orb_closes(tmp_path):
    portfolio = PortfolioEngine(tmp_path / "portfolio.json")
    position = Position(
        position_id="p1", symbol="RELIANCE", strategy="orb_5m",
        timeframe="5m", quantity=10, entry_price=100, entry_ts="now",
        stop=99, initial_stop=99, session="2024-01-01",
        active_conflict_group="opening_or_retest")
    portfolio.add_position(position)
    assert portfolio.has_active_conflict("RELIANCE", "opening_or_retest")
    portfolio.close_position(position, 99, "stop_loss")
    assert not portfolio.has_active_conflict("RELIANCE", "opening_or_retest")
