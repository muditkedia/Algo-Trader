"""High-Volume Return Premium - abnormal attention lifts the price for weeks.

Thesis (Gervais, Kaniel & Mingelgrin 2001, JF): a day of abnormally high
volume is a visibility shock - the stock enters more investors' opportunity
sets, and the widened base lifts the price over the following weeks. The
published effect is direction-unconditional; expressed long-only here. The
volume data is already in the store, making this the cheapest volume-family
test available. Pre-registered in research/PREREGISTRATION_BATCH1.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import crossed_above, volume_ratio
from algo.execution import atr_trail_swing
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import Component, ConfidenceScore, clip01, weighted


@dataclass(frozen=True)
class HvolParams:
    volume_window: int = 50
    min_ratio: float = 3.0

    @classmethod
    def from_dict(cls, data) -> "HvolParams":
        return from_dict(cls, data)


class HighVolumePremium(StrategyProfile):

    meta = StrategyMeta(
        name="hvol_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=55,
        required_columns=("close", "volume_ratio"),
        supported_regimes=("bull", "range"),
        hypothesis=("A day of abnormally high volume is an attention shock: "
                    "the stock enters more investors' opportunity sets, and "
                    "the broadened base lifts the price over the following "
                    "1-4 weeks (the high-volume return premium)."),
        expected_behaviour=("Several signals per stock per year, clustered "
                            "around news; direction-unconditional trigger."),
        known_failure_modes=(
            "US 1963-1996 evidence; attention dynamics have changed with "
            "electronic and algorithmic trading",
            "modest published effect size vs a 30.9 bps delivery round trip",
            "high-volume days include distressed selling this long-only "
            "expression cannot distinguish",
        ),
        enabled=True,
        horizon_bars=(5, 10, 20), max_hold_bars=20,
    )

    #: Swing execution owned by this strategy: ATR/structure stop,
    #: chandelier trail + profit locks, overnight allowed, horizon end
    #: at its declared max hold (no session square-off).
    execution = atr_trail_swing(meta.max_hold_bars)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or HvolParams())

    def min_history(self) -> int:
        return self.settings.volume_window + 5

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        df = dataframe.copy()
        df["volume_ratio"] = volume_ratio(df["volume"],
                                          self.settings.volume_window)
        return df

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        threshold = pd.Series(self.settings.min_ratio, index=dataframe.index)
        return crossed_above(dataframe["volume_ratio"], threshold).fillna(False)

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        ratio = float(dataframe["volume_ratio"].iloc[-1])
        extremity = clip01((ratio - self.settings.min_ratio)
                           / self.settings.min_ratio)
        return weighted(
            [Component("volume_extremity", extremity, 1.0,
                       f"volume {ratio:.1f}x its 50d mean")],
            reason="abnormal-volume attention shock")
