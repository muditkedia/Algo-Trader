"""Deterministic market frames for STRAT-03 opening-drive tests."""

from __future__ import annotations

import numpy as np
import pandas as pd


def strat03_frames(direction: str = "long", symbol: str = "RELIANCE") -> dict:
    sign = 1.0 if direction == "long" else -1.0
    equities, index = [], []
    for day_no, day in enumerate(pd.bdate_range("2024-01-01", periods=21)):
        count = 1 if day_no == 20 else 75
        dates = pd.date_range(f"{day.date()} 03:45", periods=count,
                              freq="5min", tz="UTC")
        base = (98.0 if sign > 0 else 102.0) + sign * day_no * 0.02
        close = np.linspace(base, base + sign * 0.30, count)
        open_ = np.r_[close[0], close[:-1]]
        high = np.maximum(open_, close) + 0.60
        low = np.minimum(open_, close) - 0.60
        volume = np.full(count, 500_000.0)
        if day_no == 20:
            open_ = np.array([100.0])
            close = np.array([101.30 if sign > 0 else 98.70])
            high = np.array([101.35 if sign > 0 else 100.05])
            low = np.array([99.95 if sign > 0 else 98.65])
            volume = np.array([1_500_000.0])
        equities.append(pd.DataFrame({
            "date": dates, "open": open_, "high": high, "low": low,
            "close": close, "volume": volume, "symbol": symbol}))

        nifty_open = np.full(count, 200.0 + sign * day_no)
        nifty_close = nifty_open + sign * np.linspace(0.2, 1.0, count)
        index.append(pd.DataFrame({
            "date": dates, "open": nifty_open,
            "high": np.maximum(nifty_open, nifty_close) + 0.10,
            "low": np.minimum(nifty_open, nifty_close) - 0.10,
            "close": nifty_close, "volume": np.full(count, 1_000_000.0),
            "symbol": "NIFTY50"}))
    return {symbol: pd.concat(equities, ignore_index=True),
            "NIFTY50": pd.concat(index, ignore_index=True)}
