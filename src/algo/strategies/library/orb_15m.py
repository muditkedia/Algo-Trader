"""Opening Range Breakout (ORB) with volume confirmation.

Thesis: the first minutes of the NSE session establish the day's initial
auction range; a later break of that range's high on elevated volume signals
directional conviction for the session. Volume confirmation is MANDATORY here
(part of the entry, not just the confidence) - a quiet drift through the level
is not the setup.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import atr, crossed_above, opening_range, volume_ratio
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import (
    Component, ConfidenceScore, clip01, weighted,
)


@dataclass(frozen=True)
class OrbParams:
    #: opening-range window from the session's first bar, in minutes
    range_minutes: int = 15
    #: breakout bar volume must reach this multiple of average (mandatory)
    volume_ratio_min: float = 1.5
    volume_window: int = 20
    atr_period: int = 14
    #: an opening range wider than this many ATRs is too sloppy to trade
    tight_range_atr: float = 2.0

    @classmethod
    def from_dict(cls, data) -> "OrbParams":
        return from_dict(cls, data)


class OpeningRangeBreakout(StrategyProfile):

    meta = StrategyMeta(
        name="orb_15m", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.INTRADAY,
        timeframe="15m", min_bars=30,
        required_columns=("close", "high", "low", "or_high", "or_low",
                          "after_range", "atr", "volume_ratio"),
        supported_regimes=("bull", "range"),
        hypothesis=("The opening auction range anchors the session; a "
                    "volume-confirmed break of its high marks initiative buying "
                    "that tends to persist into the session."),
        expected_behaviour=("At most one long signal per stock per session, "
                            "usually in the first half of the day."),
        known_failure_modes=(
            "open-drive days where the range never forms cleanly",
            "midday breaks with volume but no follow-through (lunch fade)",
            "wide/gappy opening ranges - breakout point is too far from value",
        ),
        enabled=True,
    )

    def __init__(self, settings=None) -> None:
        super().__init__(settings or OrbParams())

    def min_history(self) -> int:
        return max(self.settings.volume_window + 5, self.meta.min_bars)

    # ------------------------------------------------------------ indicators

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        or_high, or_low, after = opening_range(df, p.range_minutes)
        df["or_high"] = or_high
        df["or_low"] = or_low
        df["after_range"] = after
        df["atr"] = atr(df, p.atr_period)
        df["volume_ratio"] = volume_ratio(df["volume"], p.volume_window)
        return df

    # ---------------------------------------------------------------- signal

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        p = self.settings
        df = dataframe
        breakout = crossed_above(df["close"], df["or_high"])
        confirmed = df["volume_ratio"] >= p.volume_ratio_min
        return (df["after_range"] & breakout & confirmed).fillna(False)

    # ------------------------------------------------------------ confidence

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        p = self.settings
        row = dataframe.iloc[-1]
        # participation: surge beyond the mandatory floor (2x floor => 1.0)
        volume = clip01(float(row["volume_ratio"]) / (2.0 * p.volume_ratio_min))
        # range tightness: narrow opening range (in ATR) breaks cleaner
        or_range = float(row["or_high"] - row["or_low"])
        range_atr = or_range / float(row["atr"]) if row["atr"] else float("inf")
        tightness = clip01(1.0 - range_atr / p.tight_range_atr)
        # bar quality: close location within the breakout bar
        bar_range = float(row["high"] - row["low"])
        clv = clip01((float(row["close"] - row["low"]) / bar_range)
                     if bar_range > 0 else 0.0)
        return weighted(
            [Component("volume_surge", volume, 0.40,
                       f"volume_ratio {float(row['volume_ratio']):.2f} "
                       f"(min {p.volume_ratio_min})"),
             Component("range_tightness", tightness, 0.30,
                       f"opening range {range_atr:.2f} ATR"),
             Component("close_strength", clv, 0.30,
                       f"close at {clv:.0%} of bar range")],
            reason="volume-confirmed break of the opening range high",
        )
