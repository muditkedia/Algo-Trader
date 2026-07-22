"""Dual momentum - cross-sectional strength gated by an absolute trend filter.

Thesis (Antonacci 2014): combine relative momentum (top decile) with absolute
momentum (own 12-month return > 0), so the book steps aside in bear markets
where relative momentum crashes. Distinct from both tsmom (pure absolute, dead)
and xsmom (pure relative): the regime-conditional absolute gate is the point.
Pre-registered in research/PREREGISTRATION_BATCH2.md (#3).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.execution import atr_trail_swing
from algo.strategies.base import StrategyMeta
from algo.strategies.confidence import Component, ConfidenceScore, clip01, weighted
from algo.strategies.cross_section import CrossSectionalDecileStrategy, entered


@dataclass(frozen=True)
class DualMomParams:
    formation: int = 252
    skip: int = 21
    quantile: float = 0.10
    absolute_floor: float = 0.0

    @classmethod
    def from_dict(cls, data) -> "DualMomParams":
        return from_dict(cls, data)


class DualMomentum(CrossSectionalDecileStrategy):
    metric_col = "mom_12_1"
    top = True

    meta = StrategyMeta(
        name="dualmom_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=280,
        required_columns=("close", "mom_12_1", "xs_flag"),
        supported_regimes=("bull",),
        hypothesis=("Top-decile relative momentum AND positive own 12-1 return: "
                    "the absolute gate keeps the book out of bear markets where "
                    "relative momentum crashes. Regime-conditional."),
        expected_behaviour="Like xsmom but flat when its own trend is negative.",
        known_failure_modes=(
            "the absolute filter whipsaws in choppy sideways markets",
            "still long-only relative momentum underneath",
            "popularised in a trade book - treat published results as marketing",
        ),
        enabled=True,
        horizon_bars=(21, 42, 63), max_hold_bars=63,
    )

    #: Swing execution owned by this strategy: ATR/structure stop,
    #: chandelier trail + profit locks, overnight allowed, horizon end
    #: at its declared max hold (no session square-off).
    execution = atr_trail_swing(meta.max_hold_bars)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or DualMomParams())
        self.quantile = self.settings.quantile

    def min_history(self) -> int:
        return self.settings.formation + self.settings.skip + 5

    def compute_metric(self, df: pd.DataFrame) -> pd.Series:
        p = self.settings
        return df["close"].shift(p.skip) / df["close"].shift(p.formation) - 1.0

    def entry_signal(self, df: pd.DataFrame) -> pd.Series:
        if "xs_flag" not in df.columns or len(df) < self.min_history():
            return self.no_signal(df)
        # relative (top decile) AND absolute (own momentum > floor)
        combined = (df["xs_flag"] > 0.5) & (df["mom_12_1"] > self.settings.absolute_floor)
        return entered(combined.astype(float)).fillna(False)

    def confidence(self, df: pd.DataFrame) -> ConfidenceScore:
        if df.empty or "mom_12_1" not in df.columns:
            return ConfidenceScore.zero("no metric")
        mom = float(df["mom_12_1"].iloc[-1]) if df["mom_12_1"].iloc[-1] == \
            df["mom_12_1"].iloc[-1] else 0.0
        return weighted(
            [Component("dual_momentum", clip01(mom / 0.30), 1.0,
                       f"own 12-1 momentum {mom:+.1%}, top decile")],
            reason="top-decile relative momentum with positive absolute trend")
