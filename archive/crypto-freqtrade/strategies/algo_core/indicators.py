"""Shared indicator computation.

Every timeframe (5m base and 15m/1h/4h informative) is enriched with the same
canonical column set so downstream modules can address indicators uniformly.
After ``merge_informative_pair`` the informative columns carry the usual
Freqtrade suffixes (e.g. ``ema_fast_1h``, ``adx_15m``).

All computations use only the current and previous candles - no forward
references - to keep the pipeline lookahead-safe.
"""

from __future__ import annotations

import pandas as pd
import talib.abstract as ta


def add_core_indicators(
    dataframe: pd.DataFrame,
    ema_fast: int,
    ema_slow: int,
    *,
    adx_period: int = 14,
    rsi_period: int = 14,
    atr_period: int = 14,
    volume_window: int = 20,
    structure_window: int = 10,
    trend_shift: int = 6,
) -> pd.DataFrame:
    """Add the canonical indicator set to ``dataframe`` (mutates and returns it).

    Columns added:
        ema_fast / ema_slow   trend direction
        adx                   trend strength
        rsi                   momentum
        atr / atr_pct         volatility (absolute and relative to close)
        volume_mean / volume_ratio
        swing_low             rolling swing low (structure-aware stop anchor)
        structure_up          1 when the rolling swing low is rising
        closing_higher        1 when close is above the close ``trend_shift`` bars ago
    """
    dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=ema_fast)
    dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=ema_slow)
    dataframe["adx"] = ta.ADX(dataframe, timeperiod=adx_period)
    dataframe["rsi"] = ta.RSI(dataframe, timeperiod=rsi_period)
    dataframe["atr"] = ta.ATR(dataframe, timeperiod=atr_period)
    dataframe["atr_pct"] = dataframe["atr"] / dataframe["close"]

    dataframe["volume_mean"] = (
        dataframe["volume"].rolling(volume_window, min_periods=volume_window).mean()
    )
    dataframe["volume_ratio"] = dataframe["volume"] / dataframe["volume_mean"]

    swing_low = dataframe["low"].rolling(structure_window).min()
    dataframe["swing_low"] = swing_low
    dataframe["structure_up"] = (
        swing_low > swing_low.shift(structure_window)
    ).astype("int64")
    dataframe["closing_higher"] = (
        dataframe["close"] > dataframe["close"].shift(trend_shift)
    ).astype("int64")
    return dataframe


def crossed_above(series: pd.Series, reference: pd.Series) -> pd.Series:
    """True on the candle where ``series`` closes above ``reference``."""
    return (series > reference) & (series.shift(1) <= reference.shift(1))


def crossed_below(series: pd.Series, reference: pd.Series) -> pd.Series:
    """True on the candle where ``series`` closes below ``reference``."""
    return (series < reference) & (series.shift(1) >= reference.shift(1))
