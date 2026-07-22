"""Deterministic completed-bar fixtures for STRAT-02."""

from __future__ import annotations

import numpy as np
import pandas as pd


def strat02_frames(direction: str = "long", symbol: str = "RELIANCE") -> dict:
    sign = 1.0 if direction == "long" else -1.0
    equities, index = [], []
    days = pd.bdate_range("2024-01-01", periods=21)
    for day_no, day in enumerate(days):
        count = 5 if day_no == 20 else 75
        dates = pd.date_range(f"{day.date()} 03:45", periods=count,
                              freq="5min", tz="UTC")
        base = (98.0 if sign > 0 else 102.0) + sign * day_no * 0.02
        close = np.linspace(base, base + sign * 0.30, count)
        open_ = np.r_[close[0], close[:-1]]
        high = np.maximum(open_, close) + 0.60
        low = np.minimum(open_, close) - 0.60
        volume = np.full(count, 500_000.0)
        if day_no == 20:
            close = np.array([100.0, 100.7, 101.1, 100.7, 101.16]) \
                if sign > 0 else np.array([100.0, 99.3, 98.9, 99.3, 98.84])
            open_ = np.r_[100.0, close[:-1]]
            high = np.array([100.5, 100.85, 101.25, 101.15, 101.30]) \
                if sign > 0 else np.array([100.5, 100.10, 99.40, 99.45, 99.35])
            low = np.array([99.5, 99.90, 100.60, 100.55, 100.65]) \
                if sign > 0 else np.array([99.5, 99.15, 98.75, 98.85, 98.70])
            volume = np.array([1_500_000, 1_500_000, 800_000,
                               400_000, 1_500_000], dtype=float)
        equities.append(pd.DataFrame({
            "date": dates, "open": open_, "high": high, "low": low,
            "close": close, "volume": volume, "symbol": symbol}))

        nifty_close = 200.0 + sign * (day_no + np.linspace(0, 1, count))
        nifty_open = np.r_[nifty_close[0], nifty_close[:-1]]
        index.append(pd.DataFrame({
            "date": dates, "open": nifty_open,
            "high": np.maximum(nifty_open, nifty_close) + 0.10,
            "low": np.minimum(nifty_open, nifty_close) - 0.10,
            "close": nifty_close, "volume": np.full(count, 1_000_000.0),
            "symbol": "NIFTY50"}))
    return {symbol: pd.concat(equities, ignore_index=True),
            "NIFTY50": pd.concat(index, ignore_index=True)}
