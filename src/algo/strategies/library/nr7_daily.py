"""NR7 / Volatility Contraction Breakout - daily narrow-range coil, next-day break.

Thesis: a day whose range is the narrowest of the last seven (NR7) marks
short-term volatility exhaustion; the following day's break above that
narrow day's high frequently begins a directional expansion. The classic
Toby Crabel narrow-range pattern, taken long-only with volume confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import atr, crossed_above, volume_ratio
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import (
    Component, ConfidenceScore, clip01, weighted,
)


@dataclass(frozen=True)
class Nr7Params:
    #: the narrow day must have the smallest range of this many days
    nr_lookback: int = 7
    volume_ratio_min: float = 1.2
    volume_window: int = 20
    atr_period: int = 14

    @classmethod
    def from_dict(cls, data) -> "Nr7Params":
        return from_dict(cls, data)


class Nr7VolatilityContraction(StrategyProfile):

    meta = StrategyMeta(
        name="nr7_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=30,
        required_columns=("close", "high", "low", "nr_prev", "prev_high",
                          "atr", "volume_ratio"),
        supported_regimes=("bull", "range"),
        hypothesis=("Range contraction to a 7-day minimum precedes range "
                    "expansion; breaking the narrow day's high on volume "
                    "captures the expansion's first directional day."),
        expected_behaviour=("Infrequent per stock (~NR7 days are ~1 in 7 by "
                            "construction, breaks rarer); holds days."),
        known_failure_modes=(
            "expansion resolves DOWN after a marginal upside poke (trap)",
            "contraction from illiquidity rather than genuine coiling",
            "earnings/event gaps that bypass the level entirely",
        ),
        enabled=True,
    )

    def __init__(self, settings=None) -> None:
        super().__init__(settings or Nr7Params())

    def min_history(self) -> int:
        p = self.settings
        return max(p.volume_window + 5, p.nr_lookback + 5, self.meta.min_bars)

    # ------------------------------------------------------------ indicators

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        day_range = df["high"] - df["low"]
        nr = day_range <= day_range.rolling(p.nr_lookback,
                                            min_periods=p.nr_lookback).min()
        df["nr_prev"] = nr.shift(1).fillna(False).astype(bool)  # yesterday was NR7
        df["prev_high"] = df["high"].shift(1)
        df["prev_range"] = day_range.shift(1)
        df["atr"] = atr(df, p.atr_period)
        df["volume_ratio"] = volume_ratio(df["volume"], p.volume_window)
        return df

    # ---------------------------------------------------------------- signal

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        p = self.settings
        df = dataframe
        breakout = crossed_above(df["close"], df["prev_high"])
        confirmed = df["volume_ratio"] >= p.volume_ratio_min
        return (df["nr_prev"] & breakout & confirmed).fillna(False)

    # ------------------------------------------------------------ confidence

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        p = self.settings
        row = dataframe.iloc[-1]
        # contraction depth: the narrow day's range vs ATR (smaller = better)
        prev_range_atr = (float(row["prev_range"]) / float(row["atr"])
                          if row["atr"] else float("inf"))
        contraction = clip01(1.0 - prev_range_atr)
        # participation beyond the mandatory floor (2x floor => 1.0)
        volume = clip01(float(row["volume_ratio"]) / (2.0 * p.volume_ratio_min))
        # bar quality: close location within the breakout day's range
        bar_range = float(row["high"] - row["low"])
        clv = clip01((float(row["close"] - row["low"]) / bar_range)
                     if bar_range > 0 else 0.0)
        return weighted(
            [Component("contraction_depth", contraction, 0.40,
                       f"NR7 range {prev_range_atr:.2f} ATR"),
             Component("volume", volume, 0.30,
                       f"volume_ratio {float(row['volume_ratio']):.2f} "
                       f"(min {p.volume_ratio_min})"),
             Component("close_strength", clv, 0.30,
                       f"close at {clv:.0%} of day range")],
            reason="break of an NR7 day's high on volume",
        )
