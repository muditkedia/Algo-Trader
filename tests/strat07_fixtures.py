"""Deterministic completed-bar fixtures for STRAT-07."""

from __future__ import annotations

import numpy as np
import pandas as pd


def strat07_frames(direction: str = "long", symbol: str = "RELIANCE") -> dict:
    trade_sign = 1.0 if direction == "long" else -1.0
    trend_sign = -trade_sign
    equities, index = [], []
    prior_extreme = 0.0
    for day_no, day in enumerate(pd.bdate_range("2024-01-01", periods=21)):
        count = 2 if day_no == 20 else 75
        dates = pd.date_range(f"{day.date()} 03:45", periods=count,
                              freq="5min", tz="UTC")
        if day_no < 20:
            base = 100.0 + trend_sign * day_no * 0.05
            close = np.linspace(base, base + trend_sign * 0.30, count)
            open_ = np.r_[close[0], close[:-1]]
            high = np.maximum(open_, close) + 0.30
            low = np.minimum(open_, close) - 0.30
            volume = np.full(count, 500_000.0)
            prior_extreme = float(low.min() if trade_sign > 0 else high.max())
        elif trade_sign > 0:
            open_ = np.array([prior_extreme + 0.55, prior_extreme - 0.03])
            close = np.array([prior_extreme + 0.20, prior_extreme + 0.13])
            high = np.array([prior_extreme + 0.65, prior_extreme + 0.17])
            low = np.array([prior_extreme, prior_extreme - 0.29])
            volume = np.array([500_000.0, 1_200_000.0])
        else:
            open_ = np.array([prior_extreme - 0.55, prior_extreme + 0.03])
            close = np.array([prior_extreme - 0.20, prior_extreme - 0.13])
            high = np.array([prior_extreme, prior_extreme + 0.29])
            low = np.array([prior_extreme - 0.65, prior_extreme - 0.17])
            volume = np.array([500_000.0, 1_200_000.0])
        equities.append(pd.DataFrame({
            "date": dates, "open": open_, "high": high, "low": low,
            "close": close, "volume": volume, "symbol": symbol}))

        nbase = 200.0 + trend_sign * day_no * 0.10
        nclose = np.linspace(nbase, nbase + trend_sign * 0.20, count)
        nopen = np.r_[nclose[0], nclose[:-1]]
        nhigh = np.maximum(nopen, nclose) + 0.10
        nlow = np.minimum(nopen, nclose) - 0.10
        if day_no == 20:
            # The index remains inside its own first-bar range while the stock
            # performs the stop run.
            nopen = np.array([nbase, nbase])
            nclose = np.array([nbase, nbase + trade_sign * 0.02])
            nhigh = np.array([nbase + 0.10, nbase + 0.08])
            nlow = np.array([nbase - 0.10, nbase - 0.08])
        index.append(pd.DataFrame({
            "date": dates, "open": nopen, "high": nhigh, "low": nlow,
            "close": nclose, "volume": np.full(count, 1_000_000.0),
            "symbol": "NIFTY50"}))
    return {symbol: pd.concat(equities, ignore_index=True),
            "NIFTY50": pd.concat(index, ignore_index=True)}
