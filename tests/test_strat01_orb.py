"""Isolation tests for the canonical STRAT-01 ORB implementation."""

import pandas as pd
import pytest

from algo.core.costs import FlatCostModel, Product
from algo.core.enums import Direction
from algo.execution.engine import execute_signal
from algo.strategies.library import ALL_STRATEGIES, OpeningRangeBreakout
from algo.trading.models import Position
from algo.trading.orchestrator import Orchestrator
from algo.trading.trademanager import TradeManager
from strat01_fixtures import strat01_frames


class _State:
    def __init__(self, frames):
        self.frames = frames
        self.symbols = list(frames)
        self.context_symbols = ["NIFTY50"]

    def usable_symbols(self, timeframe=None):
        return list(self.symbols)

    def history(self, symbol, timeframe):
        return self.frames.get(symbol, pd.DataFrame()).copy()


@pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
def test_strat01_emits_one_directional_signal_with_collared_limit(direction):
    frames = strat01_frames(direction.value)
    signals = Orchestrator(
        [OpeningRangeBreakout()], _State(frames)).evaluate("5m")
    signal = next(s for s in signals if s.symbol == "RELIANCE")
    assert signal.strategy == "orb_5m" and signal.direction == direction
    assert signal.regime_score >= 0.60 and signal.confidence >= 0.55
    assert signal.spec.entry == "limit_collar"
    if direction == Direction.LONG:
        assert signal.limit_price > signal.trigger_price
        assert signal.stop < signal.entry_ref < signal.target
    else:
        assert signal.limit_price < signal.trigger_price
        assert signal.target < signal.entry_ref < signal.stop


def test_registry_contains_only_the_new_orb_identity():
    names = {cls.meta.name for cls in ALL_STRATEGIES}
    assert "orb_5m" in names and "orb_15m" not in names
    assert OpeningRangeBreakout.meta.spec_id == "STRAT-01"
    assert OpeningRangeBreakout.meta.direction == Direction.BOTH


def test_same_slot_rvol_excludes_the_current_session():
    frames = strat01_frames("long")
    strategy = OpeningRangeBreakout()
    prepared = strategy.prepare(frames["RELIANCE"])
    assert prepared["rvol"].iloc[-1] == pytest.approx(3.0)
    # The opening and breakout slots each compare with their own ten prior
    # sessions, not a generic rolling volume average.
    assert prepared["opening_rvol"].iloc[-1] == pytest.approx(3.0)


def test_short_backtest_books_directional_profit_and_partial():
    dates = pd.date_range("2024-03-04 03:50", periods=4, freq="5min", tz="UTC")
    bars = pd.DataFrame({
        "date": dates, "open": [100, 100, 98.6, 97.0],
        "high": [100.2, 100.2, 99.0, 97.5],
        "low": [99.8, 98.4, 96.5, 96.0],
        "close": [100, 98.6, 97.0, 96.5], "volume": 1000,
        "or_mid": 101.0, "invalidate_long": False,
        "invalidate_short": False})
    trade = execute_signal(
        bars, 0, spec=OpeningRangeBreakout.execution, atr=1.0,
        swing_low=99.0, swing_high=101.0,
        cost_model=FlatCostModel(0.0), product=Product.INTRADAY,
        stake=50_000.0, direction=Direction.SHORT)
    assert trade.direction == "short" and trade.partial
    assert trade.partial_price == pytest.approx(98.5)
    assert trade.profit_abs > 0


def test_short_manager_uses_upside_stop_and_vwap_invalidation():
    spec = OpeningRangeBreakout.execution
    manager = TradeManager({"orb_5m": spec})
    pos = Position(
        position_id="p", symbol="RELIANCE", strategy="orb_5m",
        timeframe="5m", quantity=100, open_quantity=100,
        entry_price=100, entry_ts="2024-03-04T04:00:00Z",
        stop=101, initial_stop=101, target=98.5, direction="short",
        atr_at_entry=1.0)
    bar_time = pd.Timestamp("2024-03-04T04:05:00Z")
    stopped = manager.manage(pos, pd.Series({
        "date": bar_time,
        "open": 101.5, "high": 102, "low": 100.5, "close": 101,
        "invalidate_short": False}), spec=spec)
    assert stopped.action == "exit" and stopped.price == pytest.approx(101.5)

    pos.stop = 103
    invalidated = manager.manage(pos, pd.Series({
        "date": bar_time + pd.Timedelta(minutes=5),
        "open": 100, "high": 100.5, "low": 99.0, "close": 100.2,
        "invalidate_short": True}), spec=spec)
    assert invalidated.reason == "structural_invalidation"
