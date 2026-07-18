"""Reusable research components - the building blocks of a hypothesis.

Batch 1-2 (27 strategies) showed that almost every candidate is a composition of
a small number of pieces: a per-symbol METRIC (or a direct ENTRY condition), an
optional FILTER, a RANK direction, and a HOLDING horizon. This module factors
those pieces out as reusable, parameterised callables so a new hypothesis is
DECLARED (algo.research.hypothesis) rather than hand-written as a 60-line class.

Each factory returns a ``callable(df) -> Series`` that is causal (uses only
current and prior rows). Metrics feed a cross-sectional decile sort; filters and
entries are per-symbol boolean conditions. Nothing here decides anything or
touches the frozen gate - it only reduces the cost of expressing an idea.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from algo.core.indicators import atr, crossed_above, rsi, volume_ratio

Metric = Callable[[pd.DataFrame], pd.Series]      # per-symbol ranking metric
Condition = Callable[[pd.DataFrame], pd.Series]   # per-symbol boolean


# --------------------------------------------------------------- metrics

def momentum(formation: int = 252, skip: int = 21) -> Metric:
    """Trailing return from t-formation to t-skip (the classic 12-1)."""
    return lambda df: df["close"].shift(skip) / df["close"].shift(formation) - 1.0


def volatility(window: int = 126) -> Metric:
    return lambda df: df["close"].pct_change().rolling(
        window, min_periods=window).std()


def max_daily_return(window: int = 21) -> Metric:
    return lambda df: df["close"].pct_change().rolling(
        window, min_periods=window).max()


def reversal(lookback: int = 5) -> Metric:
    return lambda df: df["close"] / df["close"].shift(lookback) - 1.0


def close_to_high(window: int = 252) -> Metric:
    return lambda df: df["close"] / df["high"].rolling(
        window, min_periods=window).max()


def amihud_illiquidity(window: int = 63) -> Metric:
    def _m(df: pd.DataFrame) -> pd.Series:
        turnover = (df["close"] * df["volume"]).replace(0.0, np.nan)
        return (df["close"].pct_change().abs() / turnover).rolling(
            window, min_periods=window).mean()
    return _m


def rsi_metric(period: int = 14) -> Metric:
    return lambda df: rsi(df["close"], period)


# --------------------------------------------------------------- filters

def min_price(floor: float = 20.0) -> Condition:
    """Exclude penny stocks (a standard liquidity/quality screen)."""
    return lambda df: df["close"] >= floor


def uptrend(ma_period: int = 200) -> Condition:
    """Price above its own long moving average (a regime filter)."""
    return lambda df: df["close"] > df["close"].rolling(
        ma_period, min_periods=ma_period).mean()


def positive_metric(metric: Metric) -> Condition:
    """Absolute filter: the metric must be > 0 (e.g. dual-momentum's own-trend)."""
    return lambda df: metric(df) > 0.0


def min_volume_ratio(mult: float = 1.5, window: int = 50) -> Condition:
    return lambda df: volume_ratio(df["volume"], window) >= mult


# --------------------------------------------------------------- entries

def new_high(window: int = 55) -> Condition:
    """Close crosses the prior ``window``-day high (breakout entry)."""
    return lambda df: crossed_above(
        df["close"], df["high"].rolling(window, min_periods=window).max().shift(1))


def gap_up(threshold: float = 0.03) -> Condition:
    """An overnight up-gap of at least ``threshold`` that holds through close."""
    def _c(df: pd.DataFrame) -> pd.Series:
        gap = df["open"] / df["close"].shift(1) - 1.0
        return (gap >= threshold) & (df["close"] >= df["open"])
    return _c


def combine_and(*conditions: Condition) -> Condition:
    """AND several per-symbol conditions into one (NaN-safe -> False)."""
    def _c(df: pd.DataFrame) -> pd.Series:
        out = pd.Series(True, index=df.index)
        for cond in conditions:
            out = out & cond(df).fillna(False)
        return out
    return _c
