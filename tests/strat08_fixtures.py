"""Deterministic completed-bar fixtures for STRAT-08."""

from __future__ import annotations

import numpy as np
import pandas as pd


def strat08_frame(direction: str = "long", symbol: str = "RELIANCE") -> pd.DataFrame:
    sign = 1.0 if direction == "long" else -1.0
    frames = []
    prior_close = 100.0
    for day_no, day in enumerate(pd.bdate_range("2024-01-01", periods=21)):
        count = 5 if day_no == 20 else 75
        dates = pd.date_range(f"{day.date()} 03:45", periods=count,
                              freq="5min", tz="UTC")
        if day_no < 20:
            base = 90.0 + sign * day_no * 0.30
            close = np.linspace(base, base + sign * 2.0, count)
            open_ = np.r_[close[0], close[:-1]]
            high = np.maximum(open_, close) + 0.18
            low = np.minimum(open_, close) - 0.18
            volume = np.full(count, 500_000.0)
        else:
            base = prior_close
            offsets = np.array([0.20, 0.50, 0.80, 0.60, 1.00]) * sign
            close = base + offsets
            open_ = np.r_[base, close[:-1]]
            open_[-1] = base + 0.70 * sign
            if sign > 0:
                high = base + np.array([0.30, 0.60, 0.90, 0.85, 1.05])
                low = base + np.array([0.00, 0.15, 0.40, 0.35, 0.35])
            else:
                high = base - np.array([0.00, 0.15, 0.40, 0.35, 0.35])
                low = base - np.array([0.30, 0.60, 0.90, 0.85, 1.05])
            volume = np.full(count, 500_000.0)
            volume[-1] = 1_000_000.0
        frames.append(pd.DataFrame({
            "date": dates, "open": open_, "high": high, "low": low,
            "close": close, "volume": volume, "symbol": symbol}))
        prior_close = float(close[-1])
    return pd.concat(frames, ignore_index=True)
