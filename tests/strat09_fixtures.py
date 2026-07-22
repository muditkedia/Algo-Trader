"""Deterministic completed-bar fixtures for STRAT-09."""

from __future__ import annotations

import numpy as np
import pandas as pd


def strat09_frames(direction: str = "long",
                   symbol: str = "RELIANCE") -> dict[str, pd.DataFrame]:
    sign = 1.0 if direction == "long" else -1.0
    equity, nifty = [], []
    prior_close = 100.0
    prior_nifty = 20_000.0
    for day_no, day in enumerate(pd.bdate_range("2024-01-01", periods=21)):
        count = 26 if day_no == 20 else 75
        dates = pd.date_range(f"{day.date()} 03:45", periods=count,
                              freq="5min", tz="UTC")
        if day_no < 20:
            phase = np.linspace(0.0, 6.0 * np.pi, count)
            close = 100.0 + sign * day_no * 0.04 + 0.18 * np.sin(phase)
            nclose = (20_000.0 + sign * day_no * 2.0
                      + 8.0 * np.sin(phase))
            volume = np.full(count, 500_000.0)
        else:
            amplitude = np.linspace(0.20, 0.001, count - 1)
            tight = amplitude * np.resize(np.array([-1.0, 1.0]), count - 1)
            close = np.r_[prior_close + tight,
                          prior_close + sign * 0.80]
            nclose = np.r_[prior_nifty + sign * np.linspace(
                0.2, 2.0, count - 1), prior_nifty + sign * 20.0]
            volume = np.full(count, 500_000.0)
            volume[-1] = 1_500_000.0 if sign > 0 else 1_750_000.0
        open_ = np.r_[close[0], close[:-1]]
        nopen = np.r_[nclose[0], nclose[:-1]]
        if day_no == 20:
            open_[-1] = prior_close + sign * 0.05
        high = np.maximum(open_, close) + 0.08
        low = np.minimum(open_, close) - 0.08
        nhigh = np.maximum(nopen, nclose) + 2.0
        nlow = np.minimum(nopen, nclose) - 2.0
        equity.append(pd.DataFrame({
            "date": dates, "open": open_, "high": high, "low": low,
            "close": close, "volume": volume, "symbol": symbol}))
        nifty.append(pd.DataFrame({
            "date": dates, "open": nopen, "high": nhigh, "low": nlow,
            "close": nclose, "volume": np.full(count, 2_000_000.0),
            "symbol": "NIFTY50"}))
        prior_close = float(close[-1])
        prior_nifty = float(nclose[-1])
    return {symbol: pd.concat(equity, ignore_index=True),
            "NIFTY50": pd.concat(nifty, ignore_index=True)}
