"""Cross-sectional momentum (12-1) - long the top-decile relative winners.

Thesis (Jegadeesh-Titman 1993; Asness-Moskowitz-Pedersen 2013; IIMA India
factors): stocks with the highest 12-month return (skipping the most recent
month) outperform the lowest over the next 1-3 months. This is the RELATIVE-rank
effect, distinct from the rejected tsmom_daily which tested a stock's own return
SIGN. Pre-registered in research/PREREGISTRATION_BATCH2.md (#1).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.strategies.base import StrategyMeta
from algo.strategies.cross_section import CrossSectionalDecileStrategy


@dataclass(frozen=True)
class XsMomParams:
    formation: int = 252
    skip: int = 21
    quantile: float = 0.10

    @classmethod
    def from_dict(cls, data) -> "XsMomParams":
        return from_dict(cls, data)


class CrossSectionalMomentum(CrossSectionalDecileStrategy):
    metric_col = "mom_12_1"
    top = True

    meta = StrategyMeta(
        name="xsmom_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=280,
        required_columns=("close", "mom_12_1", "xs_flag"),
        supported_regimes=("bull",),
        hypothesis=("Top-decile 12-1 relative momentum outperforms over 1-3 "
                    "months (cross-sectional under-reaction). Different from "
                    "tsmom's own-return sign: this ranks stocks against peers."),
        expected_behaviour="Monthly-ish turnover into the strongest names.",
        known_failure_modes=(
            "momentum crashes on sharp bear rebounds",
            "long-only truncates the short (loser) leg of the spread",
            "one market history caps independent periods",
        ),
        enabled=True,
        horizon_bars=(21, 42, 63), max_hold_bars=63,
    )

    def __init__(self, settings=None) -> None:
        super().__init__(settings or XsMomParams())
        self.quantile = self.settings.quantile

    def min_history(self) -> int:
        return self.settings.formation + self.settings.skip + 5

    def compute_metric(self, df: pd.DataFrame) -> pd.Series:
        p = self.settings
        return df["close"].shift(p.skip) / df["close"].shift(p.formation) - 1.0
