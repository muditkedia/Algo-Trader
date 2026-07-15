"""Trend Following - the active production profile.

Entry thesis: join an established multi-timeframe uptrend on a 5m
pullback-resume trigger, with trend strength (ADX) and rising market
structure confirming. High conviction over high frequency.

The profile's vectorized ``entry_signal`` applies only the MANDATORY,
candle-based gates (trend alignment across 4h/1h/15m, rising structure, and
the 5m execution trigger). The advisory signals (RSI, volume, volatility)
are evaluated per-trade by the DecisionEngine and feed the conviction score,
not this gate. All thresholds are read from ``settings`` - there are no
profile-local threshold constants, so there is one source of truth.

Timeframe roles:
    4h  regime filter        EMA alignment, price above slow EMA
    1h  intermediate trend   EMA alignment + ADX + closing higher
    15m entry trend          EMA alignment + ADX + rising structure
    5m  execution            EMA alignment + close crossing above fast EMA

Exits are delegated to the TradeManager's objective conditions (1h trend
reversal, 15m exhaustion, momentum breakdown) - never elapsed time.
"""

from __future__ import annotations

from typing import Tuple

import pandas as pd

from algo_core.indicators import crossed_above
from algo_core.profiles.base import StrategyProfile


class TrendFollowingProfile(StrategyProfile):

    name = "trend_following"
    enabled = True
    supported_regimes = ("trend",)
    required_columns = (
        "ema_fast", "ema_slow", "close",
        "ema_fast_15m", "ema_slow_15m", "adx_15m", "structure_up_15m",
        "ema_fast_1h", "ema_slow_1h", "adx_1h", "closing_higher_1h",
        "ema_fast_4h", "ema_slow_4h",
    )

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        missing = self._missing_columns(dataframe)
        if missing:
            return self._no_signal(dataframe, missing)

        engine = self.settings.engine
        df = dataframe
        regime_4h = (df["ema_fast_4h"] > df["ema_slow_4h"]) & (
            df["close"] > df["ema_slow_4h"]
        )
        trend_1h = (
            (df["ema_fast_1h"] > df["ema_slow_1h"])
            & (df["close"] > df["ema_slow_1h"])
            & (df["adx_1h"] >= engine.adx_min_1h)
            & (df["closing_higher_1h"] > 0.5)
        )
        trend_15m = (
            (df["ema_fast_15m"] > df["ema_slow_15m"])
            & (df["adx_15m"] >= engine.adx_min_15m)
            & (df["structure_up_15m"] > 0.5)
        )
        execution_5m = (df["ema_fast"] > df["ema_slow"]) & crossed_above(
            df["close"], df["ema_fast"]
        )
        return (regime_4h & trend_1h & trend_15m & execution_5m).fillna(False)

    def exit_signal(self, dataframe: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        if self.trade_manager is None:
            no_exit = pd.Series(False, index=dataframe.index)
            return no_exit, pd.Series("", index=dataframe.index)
        return self.trade_manager.exit_conditions(dataframe)
