"""Deterministic completed-bar fixtures for STRAT-10."""

from __future__ import annotations

import numpy as np
import pandas as pd


def strat10_frames(direction: str = "long",
                   symbol: str = "RELIANCE") -> dict[str, pd.DataFrame]:
    sign = 1.0 if direction == "long" else -1.0
    equity, nifty = [], []
    prior_close = 100.0
    prior_nifty = 20_000.0
    for day_no, day in enumerate(pd.bdate_range("2024-01-01", periods=21)):
        count = 21 if day_no == 20 else 75
        dates = pd.date_range(f"{day.date()} 03:45", periods=count,
                              freq="5min", tz="UTC")
        if day_no < 20:
            wave = 0.12 * np.resize(np.array([-1.0, 1.0]), count)
            close = (100.0 + sign * day_no * 0.10
                     + sign * np.linspace(0.0, 1.8, count) + wave)
            nclose = (20_000.0 + sign * day_no * 5.0
                      + sign * np.linspace(0.0, 60.0, count)
                      + 4.0 * np.resize(np.array([-1.0, 1.0]), count))
            volume = np.full(count, 500_000.0)
        else:
            residual = 0.15 * np.resize(np.array([-1.0, 1.0]), 20)
            trend = sign * 0.08 * np.arange(20)
            close = prior_close + trend + residual
            nclose = (prior_nifty + sign * 3.0 * np.arange(20)
                      + np.resize(np.array([-1.0, 1.0]), 20))
            if sign > 0:
                trigger = prior_close + 2.10
                close = np.r_[close, trigger]
                nclose = np.r_[nclose, prior_nifty + 65.0]
            else:
                trigger = prior_close - 2.10
                close = np.r_[close, trigger]
                nclose = np.r_[nclose, prior_nifty - 65.0]
            volume = np.full(count, 500_000.0)
            volume[-1] = 1_000_000.0 if sign > 0 else 1_250_000.0
        open_ = np.r_[close[0], close[:-1]]
        nopen = np.r_[nclose[0], nclose[:-1]]
        high = np.maximum(open_, close) + 0.10
        low = np.minimum(open_, close) - 0.10
        if day_no == 20:
            if sign > 0:
                open_[-1] = prior_close + 1.25
                low[-1] = prior_close + 1.15
                high[-1] = close[-1] + 0.10
            else:
                open_[-1] = prior_close - 1.25
                high[-1] = prior_close - 1.15
                low[-1] = close[-1] - 0.10
        equity.append(pd.DataFrame({
            "date": dates, "open": open_, "high": high, "low": low,
            "close": close, "volume": volume, "symbol": symbol}))
        nifty.append(pd.DataFrame({
            "date": dates, "open": nopen,
            "high": np.maximum(nopen, nclose) + 2.0,
            "low": np.minimum(nopen, nclose) - 2.0,
            "close": nclose, "volume": np.full(count, 2_000_000.0),
            "symbol": "NIFTY50"}))
        prior_close = float(close[-1])
        prior_nifty = float(nclose[-1])
    return {symbol: pd.concat(equity, ignore_index=True),
            "NIFTY50": pd.concat(nifty, ignore_index=True)}
