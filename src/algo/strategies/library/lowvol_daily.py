"""Low volatility - long the bottom-decile of trailing return volatility.

Thesis (Ang-Hodrick-Xing-Zhang 2006; Blitz-van Vliet 2007; India: Joshipura):
low-volatility stocks earn higher RISK-ADJUSTED returns than high-vol ones - a
structural (leverage-constraint) anomaly, less arbitraged than behavioural ones.
Long-only: hold the lowest-vol decile. Pre-registered in
research/PREREGISTRATION_BATCH2.md (#4).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.strategies.base import StrategyMeta
from algo.strategies.cross_section import CrossSectionalDecileStrategy


@dataclass(frozen=True)
class LowVolParams:
    window: int = 126
    quantile: float = 0.10

    @classmethod
    def from_dict(cls, data) -> "LowVolParams":
        return from_dict(cls, data)


class LowVolatility(CrossSectionalDecileStrategy):
    metric_col = "vol_126"
    top = False                         # LOWEST volatility decile

    meta = StrategyMeta(
        name="lowvol_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=140,
        required_columns=("close", "vol_126", "xs_flag"),
        supported_regimes=("bull", "range", "bear"),
        hypothesis=("Low-total-volatility stocks earn higher risk-adjusted "
                    "returns (leverage-constraint anomaly); hold the lowest-vol "
                    "decile. A risk-ranking hypothesis absent from batch 1."),
        expected_behaviour="Low turnover; defensive names; lags sharp bulls.",
        known_failure_modes=(
            "it is a RISK-ADJUSTED claim; raw returns may lag the gate's bars",
            "underperforms in strong momentum-led bulls",
            "long-only keeps only the low-vol leg",
        ),
        enabled=True,
        horizon_bars=(21, 63, 126), max_hold_bars=126,
    )

    def __init__(self, settings=None) -> None:
        super().__init__(settings or LowVolParams())
        self.quantile = self.settings.quantile

    def min_history(self) -> int:
        return self.settings.window + 10

    def compute_metric(self, df: pd.DataFrame) -> pd.Series:
        return df["close"].pct_change().rolling(
            self.settings.window, min_periods=self.settings.window).std()
