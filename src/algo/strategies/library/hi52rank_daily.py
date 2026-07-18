"""52-week-high proximity, cross-sectional RANK - long the top decile.

Thesis (George-Hwang 2004, JF): the 52-week-high effect is cross-sectional -
stocks closest to their own 52-week high (top decile of close / 52w-high)
outperform, dominating conventional momentum. This is the RANK form the paper
documents, distinct from the rejected hi52_daily which was a per-symbol absolute
THRESHOLD (crossed into the top 5% band). Different mechanism, not a variant.
Pre-registered in research/PREREGISTRATION_BATCH2.md (#8).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.strategies.base import StrategyMeta
from algo.strategies.cross_section import CrossSectionalDecileStrategy


@dataclass(frozen=True)
class Hi52RankParams:
    lookback: int = 252
    quantile: float = 0.10

    @classmethod
    def from_dict(cls, data) -> "Hi52RankParams":
        return from_dict(cls, data)


class High52WeekRank(CrossSectionalDecileStrategy):
    metric_col = "hi52_ratio"
    top = True

    meta = StrategyMeta(
        name="hi52rank_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=260,
        required_columns=("close", "hi52_ratio", "xs_flag"),
        supported_regimes=("bull",),
        hypothesis=("Stocks ranked closest to their 52-week high (top decile of "
                    "close/52w-high) outperform (George-Hwang). The RANK form, "
                    "distinct from the rejected per-symbol threshold hi52."),
        expected_behaviour="Holds the names nearest fresh highs; bull-biased.",
        known_failure_modes=(
            "market tops: everything sits near its high before a fall",
            "overlaps cross-sectional momentum (both favour strong names)",
            "long-only",
        ),
        enabled=True,
        horizon_bars=(21, 63, 126), max_hold_bars=126,
    )

    def __init__(self, settings=None) -> None:
        super().__init__(settings or Hi52RankParams())
        self.quantile = self.settings.quantile

    def min_history(self) -> int:
        return self.settings.lookback + 5

    def compute_metric(self, df: pd.DataFrame) -> pd.Series:
        prior_high = df["high"].rolling(
            self.settings.lookback, min_periods=self.settings.lookback).max()
        return df["close"] / prior_high
