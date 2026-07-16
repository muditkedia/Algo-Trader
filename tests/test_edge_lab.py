"""Edge lab: exact math on known frames + positive/negative controls.

The controls are the point: the machinery must DETECT a planted edge and
REJECT pure noise. A measurement pipeline that can't fail a strategy is
worthless (the Phase-5 mandate: eliminating weak strategies is success).
"""

import numpy as np
import pandas as pd
import pytest

from algo.research import edge_lab


def _frame(closes, start="2024-01-01", freq="1D"):
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "date": pd.date_range(start, periods=len(closes), freq=freq, tz="UTC"),
        "open": closes, "high": closes * 1.002, "low": closes * 0.998,
        "close": closes, "volume": np.full(len(closes), 100.0)})


# ------------------------------------------------------------------- math

def test_forward_returns_exact():
    r = edge_lab.forward_returns(np.array([100.0, 110.0, 121.0]), 1)
    assert r[0] == pytest.approx(0.10)
    assert r[1] == pytest.approx(0.10)
    assert np.isnan(r[2])                    # window runs off the end


def test_mfe_mae_exact():
    closes = np.array([100.0, 100.0, 100.0, 100.0])
    highs = np.array([100.0, 104.0, 102.0, 101.0])
    lows = np.array([100.0, 97.0, 99.0, 98.0])
    mfe, mae = edge_lab.mfe_mae(highs, lows, closes, window=2)
    # from bar 0, next 2 bars: high max 104, low min 97
    assert mfe[0] == pytest.approx(0.04)
    assert mae[0] == pytest.approx(-0.03)
    assert np.isnan(mfe[3])                  # excludes the entry bar itself


def test_day_bootstrap_deterministic_and_sane():
    rng = np.random.default_rng(3)
    values = pd.Series(rng.normal(0.001, 0.01, 200))
    days = pd.Series(pd.date_range("2024-01-01", periods=200, freq="6h",
                                   tz="UTC")).dt.normalize()
    lo1, hi1 = edge_lab.day_bootstrap_ci(values, days, seed=11)
    lo2, hi2 = edge_lab.day_bootstrap_ci(values, days, seed=11)
    assert (lo1, hi1) == (lo2, hi2)          # deterministic under seed
    assert lo1 < values.mean() < hi1         # CI brackets the sample mean


# ----------------------------------------------------------------- controls

def _planted_frames(n_symbols=3, n_bars=320, rise_pct=0.005, seed=5):
    """Every 20th bar is a signal; the following 4 bars rise rise_pct each."""
    frames, signals = {}, {}
    for k in range(n_symbols):
        rng = np.random.default_rng(seed + k)
        closes = [100.0 * (1 + 0.05 * k)]
        for i in range(1, n_bars):
            phase = i % 20
            if 11 <= phase <= 14:            # planted rise after signal bar
                closes.append(closes[-1] * (1 + rise_pct))
            else:
                closes.append(closes[-1] * (1 + rng.normal(0, 0.0005)))
        frame = _frame(closes)
        sym = f"P{k}"
        frames[sym] = frame
        signals[sym] = pd.Series(np.arange(n_bars) % 20 == 10,
                                 index=frame.index)
    return frames, signals


def _noise_frames(n_symbols=3, n_bars=320, seed=17):
    frames, signals = {}, {}
    for k in range(n_symbols):
        rng = np.random.default_rng(seed + k)
        closes = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n_bars)))
        frame = _frame(closes)
        sym = f"N{k}"
        frames[sym] = frame
        signals[sym] = pd.Series(np.arange(n_bars) % 9 == 4, index=frame.index)
    return frames, signals


COST = 0.0015          # 15 bps round trip (between intraday and delivery)


def test_positive_control_planted_edge_detected():
    frames, signals = _planted_frames()
    report = edge_lab.measure(frames, signals, strategy="planted",
                              cost_pct=COST, timeframe_minutes=1440)
    best = report.best()
    assert report.n_signals >= 30
    assert best.gross_mean > 0.015           # ~2% planted over 4 bars
    assert best.ci_low > COST                # lower bound clears the cost
    assert best.beats_cost
    # note: MFE/MAE vs random is NOT discriminative here - random entries on
    # the planted corpus also intersect the planted rises (the baseline
    # correctly discounts corpus-wide drift). Sanity-check the skew only.
    assert report.mfe_mae_ratio > 1.0


def test_negative_control_noise_rejected():
    frames, signals = _noise_frames()
    report = edge_lab.measure(frames, signals, strategy="noise",
                              cost_pct=COST, timeframe_minutes=1440)
    assert report.n_signals >= 30
    # noise must NOT clear the cost hurdle at any horizon
    assert not any(h.beats_cost for h in report.horizons)


def test_empty_signals_yield_empty_report():
    frames, _ = _noise_frames(n_symbols=1)
    sym = next(iter(frames))
    signals = {sym: pd.Series(False, index=frames[sym].index)}
    report = edge_lab.measure(frames, signals, strategy="never",
                              cost_pct=COST, timeframe_minutes=1440)
    assert report.n_signals == 0 and report.best() is None
