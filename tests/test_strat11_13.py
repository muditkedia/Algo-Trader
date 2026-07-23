"""Production contracts for STRAT-11, STRAT-12 and STRAT-13."""

import numpy as np
import pandas as pd
import pytest

from algo.core.enums import Direction
from algo.core.indicators import confirmed_fractal_pivots, donchian_channel
from algo.strategies.library import ALL_STRATEGIES
from algo.strategies.library.donchian_volatility_expansion_5m import (
    DonchianVolatilityExpansion, DonchianVolatilityExpansionParams,
)
from algo.strategies.library.swing_structure_trend_5m import (
    SwingStructureTrendContinuation, SwingStructureTrendParams,
)
from algo.strategies.library.volatility_contraction_5m import (
    VolatilityContractionParams, VolatilityContractionPattern,
)
from algo.trading.orchestrator import Orchestrator
from algo.trading.signals import TradingSignal


def _base_frame():
    rows = []
    for day in pd.bdate_range("2026-06-22", periods=21):
        start = (pd.Timestamp(day.date()).tz_localize("Asia/Kolkata")
                 + pd.Timedelta(hours=9, minutes=15))
        for i in range(75):
            close = 100.0 + 0.02 * np.sin(i)
            rows.append([start.tz_convert("UTC") + pd.Timedelta(minutes=5 * i),
                         close, close + 0.20, close - 0.20, close, 100_000.0])
    return pd.DataFrame(rows, columns=(
        "date", "open", "high", "low", "close", "volume"))


def _current_positions(frame):
    dates = frame["date"].dt.tz_convert("Asia/Kolkata").dt.date
    return np.flatnonzero(dates == dates.iloc[-1])


def _set_bars(frame, positions, bars):
    for offset, values in bars.items():
        frame.loc[positions[offset], ["open", "high", "low", "close"]] = values


def _dve_frame():
    frame, positions = _base_frame(), None
    positions = _current_positions(frame)
    for i, pos in enumerate(positions[:25]):
        close = 99.8 + i * 0.02
        frame.loc[pos, ["open", "high", "low", "close"]] = (
            close - 0.02, close + 0.15, close - 0.15, close)
    frame.loc[positions[25], ["open", "high", "low", "close", "volume"]] = (
        100.25, 102.50, 100.20, 102.40, 300_000.0)
    return frame


def _sstc_frame():
    frame, positions = _base_frame(), None
    positions = _current_positions(frame)
    _set_bars(frame, positions, {
        0: (100.0, 100.2, 99.8, 100.0),
        1: (100.2, 100.5, 99.9, 100.3),
        2: (100.6, 101.0, 100.4, 100.8),
        3: (100.5, 100.7, 100.1, 100.3),
        4: (99.8, 100.2, 99.3, 99.5),
        5: (99.3, 99.6, 99.0, 99.2),
        6: (99.5, 100.0, 99.2, 99.8),
        7: (100.5, 101.3, 100.2, 101.0),
        8: (101.2, 102.0, 101.0, 101.8),
        9: (101.3, 101.6, 100.8, 101.0),
        10: (100.8, 101.1, 100.3, 100.5),
        11: (100.3, 100.6, 100.0, 100.2),
        12: (100.8, 101.2, 100.5, 101.0),
        13: (101.5, 101.9, 101.3, 101.8),
        14: (102.0, 102.8, 101.9, 102.6),
        15: (102.3, 102.4, 102.05, 102.2),
        16: (102.2, 103.2, 102.1, 103.0),
    })
    frame.loc[positions[14], "volume"] = 300_000.0
    frame.loc[positions[15], "volume"] = 50_000.0
    frame.loc[positions[16], "volume"] = 250_000.0
    return frame


def _vcp_frame():
    frame = _base_frame()
    positions = _current_positions(frame)
    _set_bars(frame, positions, {
        0: (99.5, 99.8, 99.2, 99.5),
        1: (99.8, 100.0, 99.5, 99.8),
        2: (99.8, 100.0, 99.6, 99.9),
        3: (99.0, 99.5, 98.5, 99.0),
        4: (98.0, 98.5, 97.6, 98.0),
        5: (97.8, 98.2, 97.5, 97.8),
        6: (98.2, 98.7, 98.0, 98.5),
        7: (99.0, 99.5, 98.7, 99.3),
        8: (99.5, 99.8, 99.2, 99.6),
        9: (99.3, 99.5, 99.0, 99.2),
        10: (99.0, 99.2, 98.85, 99.0),
        11: (98.9, 99.1, 98.8, 98.9),
        12: (99.1, 99.4, 99.0, 99.3),
        13: (99.5, 99.8, 99.3, 99.6),
        14: (99.8, 100.8, 99.7, 100.7),
    })
    for offset in (11, 12, 13):
        frame.loc[positions[offset], "volume"] = 20_000.0
    frame.loc[positions[14], "volume"] = 400_000.0
    return frame


def _mirror(frame):
    mirrored = frame.copy()
    mirrored["open"] = 200.0 - frame["open"]
    mirrored["close"] = 200.0 - frame["close"]
    mirrored["high"] = 200.0 - frame["low"]
    mirrored["low"] = 200.0 - frame["high"]
    return mirrored


def _prepare(strategy, frame):
    prepared = strategy.prepare(frame)
    frames = {"TEST": prepared}
    strategy.prepare_context(frames, {"NIFTY50": pd.DataFrame()})
    return frames["TEST"]


def test_scanner_skips_shared_features_below_every_minimum(monkeypatch):
    short = _base_frame().iloc[:20]

    class State:
        symbols = ["SHORT"]
        context_symbols = []

        @staticmethod
        def history(symbol, timeframe):
            return short

    def should_not_prepare(frame):
        raise AssertionError("ineligible history reached feature generation")

    monkeypatch.setattr(
        "algo.trading.orchestrator.shared_5m_features", should_not_prepare)
    assert Orchestrator([DonchianVolatilityExpansion()], State()).evaluate(
        "5m") == []


@pytest.mark.parametrize("spec_id,cls", [
    ("STRAT-11", DonchianVolatilityExpansion),
    ("STRAT-12", SwingStructureTrendContinuation),
    ("STRAT-13", VolatilityContractionPattern),
])
def test_each_spec_has_one_enabled_canonical_strategy(spec_id, cls):
    matches = [item for item in ALL_STRATEGIES if item.meta.spec_id == spec_id]
    assert matches == [cls]
    assert cls.meta.enabled and cls.meta.direction == Direction.BOTH


def test_donchian_is_prior_only_and_session_scoped():
    frame = _dve_frame().tail(75).reset_index(drop=True)
    day = frame["date"].dt.tz_convert("Asia/Kolkata").dt.normalize()
    upper, lower, _ = donchian_channel(frame, 20, day)
    before = upper.iloc[25]
    frame.loc[25, "high"] = 9999.0
    changed, _, _ = donchian_channel(frame, 20, day)
    assert changed.iloc[25] == before
    assert lower.iloc[:20].isna().all()


def test_fractal_confirmation_has_exact_k_bar_lag_and_prefix_parity():
    frame = _sstc_frame().tail(75).reset_index(drop=True)
    full_high, full_low = confirmed_fractal_pivots(frame, 2)
    prefix_high, prefix_low = confirmed_fractal_pivots(frame.iloc[:20], 2)
    pd.testing.assert_series_equal(full_high.iloc[:20].reset_index(drop=True),
                                   prefix_high.reset_index(drop=True))
    pd.testing.assert_series_equal(full_low.iloc[:20].reset_index(drop=True),
                                   prefix_low.reset_index(drop=True))
    assert full_high.iloc[10] == pytest.approx(frame["high"].iloc[8])
    assert full_low.iloc[13] == pytest.approx(frame["low"].iloc[11])


@pytest.mark.parametrize("factory,strategy", [
    (_dve_frame, DonchianVolatilityExpansion(
        DonchianVolatilityExpansionParams(use_nifty_alignment=False))),
    (_sstc_frame, SwingStructureTrendContinuation(
        SwingStructureTrendParams(use_15m_structure=False,
                                  use_nifty_structure=False))),
    (_vcp_frame, VolatilityContractionPattern(
        VolatilityContractionParams(use_nifty_alignment=False))),
])
def test_long_and_short_mandatory_geometries_fire(factory, strategy):
    long_frame = _prepare(strategy, factory())
    long_signal = strategy.entry_signals(long_frame)[Direction.LONG]
    assert long_signal.any()
    long_row = long_frame.loc[long_signal].iloc[-1]

    short_strategy = type(strategy)(strategy.settings)
    short_frame = _prepare(short_strategy, _mirror(factory()))
    short_signal = short_strategy.entry_signals(short_frame)[Direction.SHORT]
    assert short_signal.any()
    short_row = short_frame.loc[short_signal].iloc[-1]
    assert long_row["close"] > long_row["vwap"]
    assert short_row["close"] < short_row["vwap"]


@pytest.mark.parametrize("cls,bars", [
    (DonchianVolatilityExpansion, 6),
    (SwingStructureTrendContinuation, 8),
    (VolatilityContractionPattern, 6),
])
def test_execution_contract_is_immediately_tradeable(cls, bars):
    spec = cls.execution
    assert spec.entry == "limit_collar"
    assert spec.target_kind == "r" and spec.target_r == 1.5
    assert spec.partial_fraction == 0.5
    assert spec.trail == "chandelier" and spec.trail_after_partial
    assert spec.no_progress_bars == bars and spec.no_progress_r == 0.4
    assert spec.intraday and not spec.allow_overnight


def test_vcp_has_explicit_same_bar_priority_over_donchian():
    common = dict(symbol="AAA", timeframe="5m",
                  bar_time=pd.Timestamp("2026-07-23 10:00", tz="UTC"),
                  entry_ref=101.0, stop=100.0, direction=Direction.LONG)
    dve = TradingSignal(strategy="donchian_volatility_expansion_5m",
                        spec=DonchianVolatilityExpansion.execution,
                        confidence=0.99, priority_score=99.0, **common)
    vcp = TradingSignal(
        strategy="volatility_contraction_5m",
        spec=VolatilityContractionPattern.execution,
        confidence=0.50, priority_score=1.0,
        simultaneous_priority_over=("donchian_volatility_expansion_5m",),
        **common)
    assert Orchestrator._resolve_signals([dve, vcp]) == [vcp]
