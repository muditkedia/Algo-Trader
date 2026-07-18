"""Amihud illiquidity premium - long the top decile of illiquidity.

Thesis (Amihud 2002): less-liquid stocks earn a premium for illiquidity. The
roadmap called this dead for NIFTY-100 (all liquid); NIFTY-500's 300-500 tail
spans a real liquidity range, so it is now testable. Illiquidity = mean(|daily
return| / traded-value) over 63 days.

Pre-registered weakness: the premium compensates for the very spread/impact the
flat 2 bps slippage model understates - if the gate passes it, the cost model
must be revisited before any deployment. Pre-registered in
research/PREREGISTRATION_BATCH2.md (#12).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.strategies.base import StrategyMeta
from algo.strategies.cross_section import CrossSectionalDecileStrategy


@dataclass(frozen=True)
class IlliqParams:
    window: int = 63
    quantile: float = 0.10

    @classmethod
    def from_dict(cls, data) -> "IlliqParams":
        return from_dict(cls, data)


class AmihudIlliquidity(CrossSectionalDecileStrategy):
    metric_col = "illiq_63"
    top = True                          # MOST illiquid decile

    meta = StrategyMeta(
        name="illiq_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=75,
        required_columns=("close", "illiq_63", "xs_flag"),
        supported_regimes=("bull", "range"),
        hypothesis=("Less-liquid stocks earn an illiquidity premium (Amihud). "
                    "NIFTY-500's tail has real liquidity spread, unlike the "
                    "NIFTY-100 where this was untestable."),
        expected_behaviour="Holds the least-liquid decile; low turnover.",
        known_failure_modes=(
            "the premium IS payment for spread/impact the cost model "
            "understates - a PASS demands a cost-model revisit before deploy",
            "illiquid names have wider real slippage than modelled",
            "capacity-constrained by construction",
        ),
        enabled=True,
        horizon_bars=(21, 63, 126), max_hold_bars=126,
    )

    def __init__(self, settings=None) -> None:
        super().__init__(settings or IlliqParams())
        self.quantile = self.settings.quantile

    def min_history(self) -> int:
        return self.settings.window + 10

    def compute_metric(self, df: pd.DataFrame) -> pd.Series:
        ret = df["close"].pct_change().abs()
        turnover = (df["close"] * df["volume"]).replace(0.0, np.nan)
        return (ret / turnover).rolling(
            self.settings.window, min_periods=self.settings.window).mean()
