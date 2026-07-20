"""15-minute Pullback Continuation - join an intraday uptrend on the resume bar.

Thesis: in an established 15m uptrend (fast EMA above slow EMA, price above the
slow EMA), a shallow pullback to the fast EMA that immediately resumes tends to
continue - buyers defending the trend's own moving average. The entry is the
RECLAIM bar (close crossing back above the fast EMA after the dip), never the
falling knife.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import atr, crossed_above, ema, rsi, volume_ratio
from algo.execution import atr_trail_intraday
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import (
    Component, ConfidenceScore, clip01, weighted,
)


@dataclass(frozen=True)
class PullbackParams:
    ema_fast: int = 20
    ema_slow: int = 50
    rsi_period: int = 14
    atr_period: int = 14
    volume_window: int = 20
    #: a pullback must have touched the fast EMA within this many PRIOR bars
    touch_lookback: int = 5
    #: healthy pullback RSI band (deeper = trend may be breaking)
    rsi_low: float = 35.0
    rsi_high: float = 60.0

    @classmethod
    def from_dict(cls, data) -> "PullbackParams":
        return from_dict(cls, data)


class PullbackContinuation15m(StrategyProfile):

    meta = StrategyMeta(
        name="pullback_15m", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.INTRADAY,
        timeframe="15m", min_bars=70,
        required_columns=("close", "low", "ema_fast", "ema_slow", "rsi",
                          "atr", "volume_ratio"),
        supported_regimes=("bull",),
        hypothesis=("Shallow pullbacks to the fast EMA inside an intact 15m "
                    "uptrend resume more often than they reverse; entering on "
                    "the reclaim bar buys continuation at a trend discount."),
        expected_behaviour=("Fires a handful of times per trending session "
                            "across the universe; quiet in ranges."),
        known_failure_modes=(
            "trend exhaustion: the 'pullback' is the first leg of a reversal",
            "midday chop: EMA whipsaw produces reclaim bars with no follow-through",
            "works poorly when the index regime is bearish (counter-tape longs)",
        ),
        enabled=True,
    )

    #: This strategy's OWN execution: no published exit is recorded for it
    #: anywhere in this repository, so it declares its pre-reset behaviour as
    #: its own - ATR/structure stop, chandelier trail + profit locks, session
    #: square-off, never overnight.
    execution = atr_trail_intraday()

    def __init__(self, settings=None) -> None:
        super().__init__(settings or PullbackParams())

    def min_history(self) -> int:
        p = self.settings
        return max(p.ema_slow * 2, p.volume_window + 5, self.meta.min_bars)

    # ------------------------------------------------------------ indicators

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        df["ema_fast"] = ema(df["close"], p.ema_fast)
        df["ema_slow"] = ema(df["close"], p.ema_slow)
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
        uptrend = (df["ema_fast"] > df["ema_slow"]) & (df["close"] > df["ema_slow"])
        # the dip: price traded at/below the fast EMA on a PRIOR bar
        touched = (df["low"] <= df["ema_fast"]).astype(float)
        touched_recently = (
            touched.shift(1).rolling(p.touch_lookback, min_periods=1).max() > 0.5
        )
        reclaim = crossed_above(df["close"], df["ema_fast"])
        return (uptrend & touched_recently & reclaim).fillna(False)

    # ------------------------------------------------------------ confidence

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        p = self.settings
        row = dataframe.iloc[-1]
        # trend strength: fast/slow EMA separation in ATR units (2 ATR => 1.0)
        sep_atr = ((row["ema_fast"] - row["ema_slow"]) / row["atr"]
                   if row["atr"] else 0.0)
        trend = clip01(sep_atr / 2.0)
        # pullback quality: RSI inside the healthy band, tapering outside it
        mid = (p.rsi_low + p.rsi_high) / 2.0
        half = (p.rsi_high - p.rsi_low) / 2.0
        outside = max(0.0, abs(float(row["rsi"]) - mid) - half)
        pullback = clip01(1.0 - outside / 15.0)
        # participation: volume on the reclaim bar (2x average => 1.0)
        volume = clip01(row["volume_ratio"] / 2.0)
        return weighted(
            [Component("trend_strength", trend, 0.40,
                       f"ema separation {sep_atr:+.2f} ATR"),
             Component("pullback_quality", pullback, 0.30,
                       f"rsi {row['rsi']:.1f} vs band "
                       f"{p.rsi_low:.0f}-{p.rsi_high:.0f}"),
             Component("volume", volume, 0.30,
                       f"volume_ratio {row['volume_ratio']:.2f}")],
            reason="15m uptrend pullback reclaimed the fast EMA",
        )
