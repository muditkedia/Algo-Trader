"""Deterministic completed-bar fixtures for STRAT-06."""

from __future__ import annotations

import numpy as np
import pandas as pd


def strat06_frames(direction: str = "long", symbol: str = "RELIANCE") -> dict:
    sign = 1.0 if direction == "long" else -1.0
    equities, index = [], []
    for day_no, day in enumerate(pd.bdate_range("2024-01-01", periods=21)):
        count = 7 if day_no == 20 else 75
        dates = pd.date_range(f"{day.date()} 03:45", periods=count,
                              freq="5min", tz="UTC")
        base = 98.0 + sign * day_no * 0.04
        if day_no < 20:
            close = np.linspace(base, base + sign * 0.30, count)
            open_ = np.r_[close[0], close[:-1]]
            high = np.maximum(open_, close) + 0.35
            low = np.minimum(open_, close) - 0.35
            volume = np.full(count, 500_000.0)
        else:
            center = base
            close = center + sign * np.array(
                [0.10, -0.05, 0.20, 0.00, 0.25, 0.15, 0.85])
            open_ = np.r_[center, close[:-1]]
            high = np.maximum(open_, close) + 0.20
            low = np.minimum(open_, close) - 0.20
            volume = np.full(count, 500_000.0)
            volume[-1] = 1_200_000.0 if sign > 0 else 1_400_000.0
        equities.append(pd.DataFrame({
            "date": dates, "open": open_, "high": high, "low": low,
            "close": close, "volume": volume, "symbol": symbol}))

        nbase = 200.0 + sign * day_no * 0.25
        if day_no < 20:
            nclose = np.linspace(nbase, nbase + sign * 0.80, count)
            nopen = np.r_[nclose[0], nclose[:-1]]
        else:
            nclose = nbase + sign * np.array(
                [0.1, 0.0, 0.2, 0.1, 0.25, 0.2, 1.0])
            nopen = np.r_[nbase, nclose[:-1]]
        index.append(pd.DataFrame({
            "date": dates, "open": nopen,
            "high": np.maximum(nopen, nclose) + 0.10,
            "low": np.minimum(nopen, nclose) - 0.10,
            "close": nclose, "volume": np.full(count, 1_000_000.0),
            "symbol": "NIFTY50"}))
    return {symbol: pd.concat(equities, ignore_index=True),
            "NIFTY50": pd.concat(index, ignore_index=True)}
