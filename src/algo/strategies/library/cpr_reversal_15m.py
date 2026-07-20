"""CPR Reversal - buy the S1 rejection on a rotational (wide-CPR) day.

Thesis (Ochoa, *Secrets of a Pivot Boss* - the rotational-day playbook that
complements the CPR breakout; floor-pivot bounce trading generally, one of
the oldest documented intraday methods): a WIDE prior-day CPR forecasts a
rotational session - price oscillates around the value area instead of
trending. On such days the trade is the opposite of a breakout: buy the
rejection of the S1 floor-pivot support and target a rotation back to the
central pivot. This is the library's FIRST intraday mean-reversion strategy
(everything else is continuation/breakout), and the day-type gate is the
mirror image of `cpr_breakout_15m`'s narrow-day tell.

Canonical rules implemented (no optimisation): prior-day CPR width WIDER
than its trailing 20-session median (rotational-day gate, prior sessions
only); price tags S1 (low <= S1) and REJECTS it (close back above S1 - the
same-bar rejection, or the delayed close back through S1); entry at the
rejection close; stop below the rejection bar's low; target the central
floor pivot (rotation to value); square-off; never overnight.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import (
    atr, central_pivot_range, crossed_above, floor_pivot_levels,
    floor_pivot_supports, volume_ratio,
)
from algo.execution import structural_intraday
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import Component, ConfidenceScore, clip01, \
    weighted


@dataclass(frozen=True)
class CprReversalParams:
    #: trailing sessions for the CPR-width median (the wide-day reference)
    width_window: int = 20
    atr_period: int = 14
    volume_window: int = 20

    @classmethod
    def from_dict(cls, data) -> "CprReversalParams":
        return from_dict(cls, data)


class CprReversal(StrategyProfile):

    meta = StrategyMeta(
        name="cpr_reversal_15m", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.INTRADAY,
        timeframe="15m", min_bars=30,
        required_columns=("close", "low", "cpr_wide", "fp_pivot", "fp_s1",
                          "atr", "volume_ratio"),
        supported_regimes=("range",),
        hypothesis=("A wide prior-day CPR forecasts rotation, not trend; the "
                    "rejection of S1 on such a day is responsive buying at "
                    "the range's edge, and price rotates back toward the "
                    "central pivot."),
        expected_behaviour=("Only on wide-CPR days whose open rotates down "
                            "to S1 - a few signals per stock per month."),
        known_failure_modes=(
            "genuine trend-down days that slice S1 (the gate helps, cannot "
            "eliminate them)",
            "rejections late in the session with no room to rotate back",
            "S1 far from the pivot: the target is distant on huge prior "
            "ranges",
        ),
        enabled=True,
    )

    #: CPR Reversal's OWN execution (the rotational playbook): stop below the
    #: rejection bar's low (the bar's own structural level), target the
    #: central floor pivot, no trail, session square-off, never overnight.
    execution = structural_intraday(stop_col="low", target_kind="column",
                                    target_col="fp_pivot")

    def __init__(self, settings=None) -> None:
        super().__init__(settings or CprReversalParams())

    def min_history(self) -> int:
        # width median needs ~width_window completed sessions of 15m bars
        return max(self.settings.volume_window + 5, self.meta.min_bars)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        day = df["date"].dt.normalize()
        _, cpr_top, cpr_bot = central_pivot_range(df)
        width = (cpr_top - cpr_bot).astype(float)
        # per-session width series -> trailing median of PRIOR sessions
        session_width = width.groupby(day).first()
        trailing_median = session_width.rolling(
            p.width_window, min_periods=max(5, p.width_window // 2)).median()
        # today's width (already prior-day-derived) vs the median of the
        # widths BEFORE it - shift so today's own width never enters its
        # reference
        df["cpr_wide"] = (day.map(session_width)
                          > day.map(trailing_median.shift(1))).astype(float)
        pivot, _, _ = floor_pivot_levels(df)
        s1, _ = floor_pivot_supports(df)
        df["fp_pivot"] = pivot
        df["fp_s1"] = s1
        df["atr"] = atr(df, p.atr_period)
        df["volume_ratio"] = volume_ratio(df["volume"], p.volume_window)
        return df

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        df = dataframe
        wide = df["cpr_wide"] > 0.5
        has_levels = df["fp_s1"].notna() & df["fp_pivot"].notna()
        # same-bar rejection: the low tags S1, the close holds above it -
        # fire on the FIRST rejection of an excursion, not every hugging bar
        rejection = (df["low"] <= df["fp_s1"]) & (df["close"] > df["fp_s1"])
        first_rejection = rejection & ~rejection.shift(1, fill_value=False)
        # delayed form: close reclaims S1 after having closed below it
        reclaim = crossed_above(df["close"], df["fp_s1"])
        return (wide & has_levels
                & (first_rejection | reclaim)).fillna(False)

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        row = dataframe.iloc[-1]
        # room to rotate: distance to the pivot target, in ATR units
        room = ((float(row["fp_pivot"]) - float(row["close"]))
                / float(row["atr"])) if row["atr"] and pd.notna(
            row["fp_pivot"]) else 0.0
        rejection_depth = ((float(row["fp_s1"]) - float(row["low"]))
                           / float(row["atr"])) if row["atr"] and pd.notna(
            row["fp_s1"]) else 0.0
        return weighted(
            [Component("rotation_room", clip01(room / 2.0), 0.5,
                       f"{room:.2f} ATR to the pivot"),
             Component("rejection_depth", clip01(rejection_depth / 0.5), 0.5,
                       f"tagged {rejection_depth:.2f} ATR through S1")],
            reason="S1 rejected on a wide-CPR (rotational) day")
