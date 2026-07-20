"""VWAP Pullback - buy the bounce off a rising session VWAP as support.

Thesis: on a session trending up along a RISING VWAP, a pullback that tags VWAP
as dynamic support and bounces WITHOUT the close losing VWAP is buyers defending
the institutional benchmark - a continuation entry at a discount.

Distinct from vwap_15m (VWAP Trend Continuation), which buys the RECLAIM after
price closes BELOW VWAP and crosses back above. This buys the BOUNCE where the
LOW tags VWAP but the CLOSE never loses it - a shallower pullback-to-support, a
different mechanism. Pre-registered in research/INTRADAY_PRODUCTION_BATCH1.md (#2).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import atr, crossed_above, session_vwap, volume_ratio
from algo.execution import structural_intraday
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import Component, ConfidenceScore, clip01, weighted


@dataclass(frozen=True)
class VwapPullbackParams:
    #: the low must tag within this fraction above VWAP to count as a support test
    tag_band: float = 0.002
    #: the support test must have occurred within this many PRIOR bars
    touch_lookback: int = 4
    #: bars into the session before VWAP/pullback logic is meaningful
    warmup_bars: int = 4
    atr_period: int = 14
    volume_window: int = 20

    @classmethod
    def from_dict(cls, data) -> "VwapPullbackParams":
        return from_dict(cls, data)


class VwapPullback(StrategyProfile):

    meta = StrategyMeta(
        name="vwap_pullback_15m", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.INTRADAY,
        timeframe="15m", min_bars=30,
        required_columns=("close", "high", "low", "vwap", "vwap_rising",
                          "tagged_support", "session_bar", "atr", "volume_ratio"),
        supported_regimes=("bull",),
        hypothesis=("On a rising-VWAP session, a pullback that tags VWAP as "
                    "support and bounces without the close losing VWAP is "
                    "trend-continuation demand at the institutional benchmark."),
        expected_behaviour=("Zero to a few signals per trending session; only "
                            "after a genuine tag-and-hold of VWAP."),
        known_failure_modes=(
            "VWAP support fails on distribution days (first tag breaks)",
            "flat/choppy VWAP where the tag is noise, not a pullback",
            "late-session bounces with no time before square-off",
        ),
        enabled=True,
    )

    #: This strategy's OWN execution (its published form, recorded in the
    #: Phase-17 fidelity audit): stop just below VWAP (the tagged support
    #: level, frozen at entry), 2R target, no trail, session square-off,
    #: never overnight.
    execution = structural_intraday(stop_col="vwap", target_kind="r",
                                    target_r=2.0)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or VwapPullbackParams())

    def min_history(self) -> int:
        return max(self.settings.volume_window + 5, self.meta.min_bars)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        df["vwap"] = session_vwap(df)
        day = df["date"].dt.normalize()
        df["session_bar"] = df.groupby(day).cumcount()
        # VWAP rising within the session (diff undefined at the session's first
        # bar; the warmup gate keeps those out)
        df["vwap_rising"] = (df["vwap"].groupby(day).diff() > 0).astype(float)
        # support test: the LOW tagged VWAP's band while the CLOSE held above it
        tagged = ((df["low"] <= df["vwap"] * (1.0 + p.tag_band))
                  & (df["close"] > df["vwap"])).astype(float)
        df["tagged_support"] = (
            tagged.shift(1).groupby(day)
            .rolling(p.touch_lookback, min_periods=1).max()
            .reset_index(level=0, drop=True) > 0.5).astype(float)
        df["atr"] = atr(df, p.atr_period)
        df["volume_ratio"] = volume_ratio(df["volume"], p.volume_window)
        return df

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        p = self.settings
        df = dataframe
        above = df["close"] > df["vwap"]
        rising = df["vwap_rising"] > 0.5
        tagged = df["tagged_support"] > 0.5
        mature = df["session_bar"] >= p.warmup_bars
        resume = crossed_above(df["close"], df["high"].shift(1))
        return (above & rising & tagged & mature & resume).fillna(False)

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        row = dataframe.iloc[-1]
        # bounce strength: distance re-established above VWAP in ATR units
        strength = clip01(((float(row["close"]) - float(row["vwap"]))
                           / float(row["atr"])) / 0.5 if row["atr"] else 0.0)
        volume = clip01(float(row["volume_ratio"]) / 2.0)
        return weighted(
            [Component("bounce_strength", strength, 0.6,
                       "close above VWAP after the support tag"),
             Component("volume", volume, 0.4,
                       f"volume_ratio {float(row['volume_ratio']):.2f}")],
            reason="bounce off rising-VWAP support")
