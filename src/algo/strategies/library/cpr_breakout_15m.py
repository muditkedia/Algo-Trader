"""CPR Breakout - break above the prior day's Central Pivot Range on volume.

Thesis: the Central Pivot Range (CPR), computed from the PRIOR day's H/L/C, marks
the session's expected value area. Price breaking ABOVE the top central level
signals a trend-up day - one of the most widely-used intraday setups among Indian
discretionary traders. A NARROW prior-day CPR is the higher-conviction trend
setup (used for confidence, not as a gate). Pre-registered in
research/INTRADAY_PRODUCTION_BATCH1.md (#4).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import (
    atr, central_pivot_range, crossed_above, floor_pivot_levels, opening_range,
    volume_ratio,
)
from algo.execution import structural_intraday
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import Component, ConfidenceScore, clip01, weighted


@dataclass(frozen=True)
class CprBreakoutParams:
    range_minutes: int = 15
    volume_ratio_min: float = 1.5
    volume_window: int = 20
    atr_period: int = 14
    #: a prior-day CPR narrower than this (TC-BC in ATRs) is a trend-day tell
    narrow_cpr_atr: float = 0.5

    @classmethod
    def from_dict(cls, data) -> "CprBreakoutParams":
        return from_dict(cls, data)


class CprBreakout(StrategyProfile):

    meta = StrategyMeta(
        name="cpr_breakout_15m", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.INTRADAY,
        timeframe="15m", min_bars=30,
        required_columns=("close", "cpr_top", "cpr_bot", "after_range",
                          "atr", "volume_ratio"),
        supported_regimes=("bull", "range"),
        hypothesis=("The prior day's Central Pivot Range marks the value area; a "
                    "volume-confirmed break above its top central level signals a "
                    "trend-up day."),
        expected_behaviour=("At most one long per stock per session, usually "
                            "early; nothing on the first day (no prior CPR)."),
        known_failure_modes=(
            "wide prior-day CPR -> the break is deep in the range, low edge",
            "gap-up opens already above the CPR (no clean break bar)",
            "midday breaks that fade (lunch)",
        ),
        enabled=True,
    )

    #: This strategy's OWN execution (the published CPR-trade form,
    #: research/INTRADAY_PRODUCTION_BATCH1.md section 4 + Phase-17 audit):
    #: stop below the CPR bottom, first target at the prior-day floor-pivot
    #: R1 where HALF is booked and the stop moves to breakeven, remainder
    #: runs to R2 / stop / square-off. No trail, never overnight.
    execution = structural_intraday(
        stop_col="cpr_bot", target_kind="column", target_col="fp_r1",
        partial_fraction=0.5, target2_col="fp_r2")

    def __init__(self, settings=None) -> None:
        super().__init__(settings or CprBreakoutParams())

    def min_history(self) -> int:
        return max(self.settings.volume_window + 5, self.meta.min_bars)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        _, cpr_top, cpr_bot = central_pivot_range(df)
        df["cpr_top"] = cpr_top
        df["cpr_bot"] = cpr_bot
        # prior-day floor-pivot targets (this strategy's published R1/R2 ladder)
        _, fp_r1, fp_r2 = floor_pivot_levels(df)
        df["fp_r1"] = fp_r1
        df["fp_r2"] = fp_r2
        _, _, after = opening_range(df, p.range_minutes)
        df["after_range"] = after
        df["atr"] = atr(df, p.atr_period)
        df["volume_ratio"] = volume_ratio(df["volume"], p.volume_window)
        return df

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        p = self.settings
        df = dataframe
        breakout = crossed_above(df["close"], df["cpr_top"])
        confirmed = df["volume_ratio"] >= p.volume_ratio_min
        has_cpr = df["cpr_top"].notna()
        return (df["after_range"] & breakout & confirmed & has_cpr).fillna(False)

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        p = self.settings
        row = dataframe.iloc[-1]
        volume = clip01(float(row["volume_ratio"]) / (2.0 * p.volume_ratio_min))
        cpr_width = float(row["cpr_top"] - row["cpr_bot"]) \
            if pd.notna(row["cpr_top"]) else float("inf")
        width_atr = cpr_width / float(row["atr"]) if row["atr"] else float("inf")
        narrow = clip01(1.0 - width_atr / p.narrow_cpr_atr)
        return weighted(
            [Component("volume_surge", volume, 0.5,
                       f"volume_ratio {float(row['volume_ratio']):.2f}"),
             Component("narrow_cpr", narrow, 0.5,
                       f"prior CPR {width_atr:.2f} ATR wide")],
            reason="volume-confirmed break above the prior-day CPR top")
