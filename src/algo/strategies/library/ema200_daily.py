"""200 EMA Pullback Trend Continuation - buy the long-term trend at its anchor.

Thesis: the rising 200-day EMA is the market's most watched long-term trend
anchor; in an established uptrend, deep pullbacks into its neighbourhood are
where longer-horizon buyers defend. Entry waits for the pullback to END (close
crossing back above the fast EMA) rather than knife-catching the touch itself.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import atr, crossed_above, ema, rsi, volume_ratio
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import (
    Component, ConfidenceScore, clip01, weighted,
)


@dataclass(frozen=True)
class Ema200Params:
    ema_trend: int = 200
    ema_fast: int = 20
    #: trend EMA must be higher than this many bars ago (rising anchor)
    slope_lookback: int = 20
    #: pullback low must come within this fraction above the trend EMA
    proximity_band: float = 0.03
    #: ... within this many PRIOR bars
    touch_lookback: int = 10
    rsi_period: int = 14
    atr_period: int = 14
    volume_window: int = 20

    @classmethod
    def from_dict(cls, data) -> "Ema200Params":
        return from_dict(cls, data)


class Ema200PullbackTrend(StrategyProfile):

    meta = StrategyMeta(
        name="ema200_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=240,
        required_columns=("close", "low", "ema_trend", "ema_fast", "trend_slope",
                          "rsi", "atr", "volume_ratio"),
        supported_regimes=("bull",),
        hypothesis=("Deep pullbacks toward a rising 200-day EMA in an intact "
                    "uptrend are defended by longer-horizon buyers; entering on "
                    "the resumption cross rejoins the primary trend near its "
                    "anchor with defined structure below."),
        expected_behaviour=("A few signals per stock per YEAR; the slowest, "
                            "most selective strategy in the library."),
        known_failure_modes=(
            "trend termination: the 200 EMA test that finally fails",
            "sideways multi-month bases where the EMA flattens and whipsaws",
            "slow bleed names where 'rising EMA' lags an already-dead trend",
        ),
        enabled=True,
    )

    def __init__(self, settings=None) -> None:
        super().__init__(settings or Ema200Params())

    def min_history(self) -> int:
        # Parameter-driven so reduced test params need proportionally less
        # history; with defaults this evaluates to 230 bars (~11 months).
        p = self.settings
        return max(p.ema_trend + p.slope_lookback + 10, p.volume_window + 5)

    # ------------------------------------------------------------ indicators

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        df["ema_trend"] = ema(df["close"], p.ema_trend)
        df["ema_fast"] = ema(df["close"], p.ema_fast)
        df["trend_slope"] = df["ema_trend"] - df["ema_trend"].shift(p.slope_lookback)
        df["rsi"] = rsi(df["close"], p.rsi_period)
        df["atr"] = atr(df, p.atr_period)
        df["volume_ratio"] = volume_ratio(df["volume"], p.volume_window)
        return df

    # ---------------------------------------------------------------- signal

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        p = self.settings
        df = dataframe
        uptrend = (df["close"] > df["ema_trend"]) & (df["trend_slope"] > 0)
        near_anchor = (df["low"]
                       <= df["ema_trend"] * (1.0 + p.proximity_band)).astype(float)
        touched_recently = (
            near_anchor.shift(1).rolling(p.touch_lookback, min_periods=1).max()
            > 0.5
        )
        resume = crossed_above(df["close"], df["ema_fast"])
        return (uptrend & touched_recently & resume).fillna(False)

    # ------------------------------------------------------------ confidence

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        p = self.settings
        df = dataframe
        row = df.iloc[-1]
        # anchor slope: rise over the lookback, in ATR units (1 ATR => 1.0)
        slope_atr = (float(row["trend_slope"]) / float(row["atr"])
                     if row["atr"] else 0.0)
        slope = clip01(slope_atr)
        # pullback proximity: how near the anchor the recent low actually came
        window = df["low"].tail(p.touch_lookback + 1)
        nearest = float((window / row["ema_trend"]).min()) - 1.0
        proximity = clip01(1.0 - max(0.0, nearest) / p.proximity_band
                           if p.proximity_band else 0.0)
        # momentum recovering: RSI back in the 45-65 recovery zone
        rsi_val = float(row["rsi"])
        recovery = clip01(1.0 - abs(rsi_val - 55.0) / 20.0)
        # participation on the resumption bar
        volume = clip01(float(row["volume_ratio"]) / 2.0)
        return weighted(
            [Component("anchor_slope", slope, 0.30,
                       f"trend EMA rose {slope_atr:.2f} ATR over "
                       f"{p.slope_lookback} bars"),
             Component("pullback_proximity", proximity, 0.30,
                       f"low came within {max(0.0, nearest):.2%} of the anchor"),
             Component("rsi_recovery", recovery, 0.20, f"rsi {rsi_val:.1f}"),
             Component("volume", volume, 0.20,
                       f"volume_ratio {float(row['volume_ratio']):.2f}")],
            reason="resumption after a pullback into the rising 200-EMA zone",
        )
