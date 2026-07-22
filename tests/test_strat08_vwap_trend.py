"""STRAT-08 canonical replacement, geometry, and execution tests."""

from __future__ import annotations

import pandas as pd
import pytest

from algo.core.enums import Direction
from algo.strategies.library import ALL_STRATEGIES, VwapTrendContinuation
from algo.trading.orchestrator import Orchestrator
from tests.strat08_fixtures import strat08_frame


class _State:
    context_symbols = []

    def __init__(self, frame):
        self.frame = frame

    def usable_symbols(self, timeframe):
        return ["RELIANCE"]

    def history(self, symbol, timeframe, bars=None):
        return self.frame.tail(bars) if bars else self.frame


@pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
def test_strat08_emits_bidirectional_vwap_continuation(direction):
    frame = strat08_frame(direction.value)
    signal = Orchestrator(
        [VwapTrendContinuation()], _State(frame)).evaluate("5m")[0]
    row = VwapTrendContinuation().prepare(frame).iloc[-1]
    assert signal.strategy == "vwap_trend_5m"
    assert signal.direction == direction
    assert signal.grade_multiplier == 1.0
    assert signal.priority_score == pytest.approx(
        0.50 * abs(row["vwap_slope_pct"]) + 0.50 * row["rvol"])
    assert signal.spec.entry == "limit_collar"
    assert signal.stop < signal.entry_ref if direction == Direction.LONG \
        else signal.stop > signal.entry_ref


def test_strat08_rejects_a_flat_vwap():
    frame = strat08_frame("long")
    start = len(frame) - 5
    frame.loc[start:, ["open", "high", "low", "close"]] = [100, 100.2, 99.8, 100]
    assert Orchestrator(
        [VwapTrendContinuation()], _State(frame)).evaluate("5m") == []


def test_strat08_replaces_both_legacy_vwap_modules():
    matches = [c for c in ALL_STRATEGIES if c.meta.spec_id == "STRAT-08"]
    assert matches == [VwapTrendContinuation]
    names = {c.meta.name for c in ALL_STRATEGIES}
    assert "vwap_15m" not in names
    assert "vwap_pullback_15m" not in names
