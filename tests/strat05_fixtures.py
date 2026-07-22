"""Deterministic completed-bar fixtures for STRAT-05."""

from __future__ import annotations

import numpy as np
import pandas as pd


def strat05_frames(direction: str = "long", symbol: str = "RELIANCE") -> dict:
    sign = 1.0 if direction == "long" else -1.0
    equities, index = [], []
    prior_equity_close = prior_nifty_close = 0.0
    for day_no, day in enumerate(pd.bdate_range("2024-01-01", periods=21)):
        count = 4 if day_no == 20 else 75
        dates = pd.date_range(f"{day.date()} 03:45", periods=count,
                              freq="5min", tz="UTC")
        if day_no < 20:
            base = 98.0 + sign * day_no * 0.04
            close = np.linspace(base, base + sign * 0.30, count)
            open_ = np.r_[close[0], close[:-1]]
            high = np.maximum(open_, close) + 0.35
            low = np.minimum(open_, close) - 0.35
            volume = np.full(count, 500_000.0)
        else:
            opening = prior_equity_close * (1.0 + sign * 0.015)
            if sign > 0:
                open_ = opening + np.array([0.00, 0.20, -0.25, -0.28])
                close = opening + np.array([0.30, -0.25, -0.28, -0.08])
                high = opening + np.array([0.40, 0.25, -0.15, 0.02])
                low = opening + np.array([-0.05, -0.40, -0.40, -0.35])
            else:
                open_ = opening - np.array([0.00, 0.20, -0.25, -0.28])
                close = opening - np.array([0.30, -0.25, -0.28, -0.08])
                high = opening - np.array([-0.05, -0.40, -0.40, -0.35])
                low = opening - np.array([0.40, 0.25, -0.15, 0.02])
            volume = np.array([100_000.0, 100_000.0,
                               50_000_000.0, 1_200_000.0])
        frame = pd.DataFrame({
            "date": dates, "open": open_, "high": high, "low": low,
            "close": close, "volume": volume, "symbol": symbol})
        equities.append(frame)
        prior_equity_close = float(close[-1])

        if day_no < 20:
            nbase = 200.0 + sign * day_no * 0.25
            nclose = np.linspace(nbase, nbase + sign * 0.80, count)
            nopen = np.r_[nclose[0], nclose[:-1]]
        else:
            nopen0 = prior_nifty_close * (1.0 + sign * 0.004)
            nopen = nopen0 + sign * np.array([0.0, 0.1, 0.2, 0.3])
            nclose = nopen + sign * 0.25
        nframe = pd.DataFrame({
            "date": dates, "open": nopen,
            "high": np.maximum(nopen, nclose) + 0.10,
            "low": np.minimum(nopen, nclose) - 0.10,
            "close": nclose, "volume": np.full(count, 1_000_000.0),
            "symbol": "NIFTY50"})
        index.append(nframe)
        prior_nifty_close = float(nclose[-1])
    return {symbol: pd.concat(equities, ignore_index=True),
            "NIFTY50": pd.concat(index, ignore_index=True)}
