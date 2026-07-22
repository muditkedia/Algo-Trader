"""Deterministic 5-minute market frames for STRAT-01 integration tests."""

from __future__ import annotations

import numpy as np
import pandas as pd


def strat01_frames(direction: str = "long", symbol: str = "RELIANCE") -> dict:
    sign = 1.0 if direction == "long" else -1.0
    equities, index = [], []
    days = pd.bdate_range("2024-01-01", periods=21)
    for day_no, day in enumerate(days):
        count = 2 if day_no == 20 else 75
        dates = pd.date_range(f"{day.date()} 03:45", periods=count,
                              freq="5min", tz="UTC")
        base = (98.0 if sign > 0 else 102.0) + sign * day_no * 0.02
        close = np.linspace(base, base + sign * 0.30, count)
        open_ = np.r_[close[0], close[:-1]]
        # Completed sessions retain daily NATR above STRAT-01's 1% universe
        # floor while the current-session OR remains independently controlled.
        high = np.maximum(open_, close) + 0.60
        low = np.minimum(open_, close) - 0.60
        volume = np.full(count, 500_000.0)
        if day_no == 20:
            open_[0] = close[0] = 100.0
            high[0], low[0], volume[0] = 100.5, 99.5, 1_500_000.0
            open_[1], close[1], volume[1] = (100.0,
                100.65 if sign > 0 else 99.35, 1_500_000.0)
            high[1] = max(open_[1], close[1]) + 0.60
            low[1] = min(open_[1], close[1]) - 0.60
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
