"""Wyckoff Spring - the failed breakdown that traps sellers.

Thesis (Wyckoff 1931; Pruden; Grimes' "failure test"): a break below a
multi-week range low that snaps back inside within a few bars means the
breakdown found no follow-through supply - sellers (and triggered stops) were
absorbed by larger buyers at exactly the level everyone watched. The trapped
shorts' covering plus the demonstrated absorption fuel the markup. The signal
is the FAILURE of the move - a different information source from trend,
momentum or compression. Only the mechanical core is encoded; Wyckoff's
narrative phases are deliberately not (they would test my encoding, not him).
Pre-registered in research/PREREGISTRATION_BATCH1.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import crossed_above, volume_ratio
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import Component, ConfidenceScore, clip01, weighted


@dataclass(frozen=True)
class SpringParams:
    range_window: int = 30      # the "well-tested range low" lookback
    reclaim_bars: int = 3       # breakdown bar + up to 2 more to reclaim
    volume_window: int = 20     # confidence only

    @classmethod
    def from_dict(cls, data) -> "SpringParams":
        return from_dict(cls, data)


class WyckoffSpring(StrategyProfile):

    meta = StrategyMeta(
        name="wyckoff_spring_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=40,
        required_columns=("close", "low", "range_low"),
        supported_regimes=("range", "bull"),
        hypothesis=("A break below a 30-day range low that closes back inside "
                    "within 3 bars trapped breakdown sellers at a level where "
                    "buyers absorbed the supply; their covering plus the "
                    "failed-move information fuels the markup out of the "
                    "range."),
        expected_behaviour=("Rare: springs need a range AND a failed break - "
                            "roughly one to three per stock per year."),
        known_failure_modes=(
            "genuine breakdowns that reclaim briefly before failing again",
            "practitioner-only evidence; no peer-reviewed isolation exists",
            "range/reclaim parameters are an encoding surface - frozen at "
            "pre-registration and never tuned",
        ),
        enabled=True,
        horizon_bars=(5, 10, 20, 40), max_hold_bars=40,
    )

    def __init__(self, settings=None) -> None:
        super().__init__(settings or SpringParams())

    def min_history(self) -> int:
        p = self.settings
        return p.range_window + p.reclaim_bars + 5

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        df["range_low"] = df["low"].rolling(
            p.range_window, min_periods=p.range_window).min().shift(1)
        df["volume_ratio"] = volume_ratio(df["volume"], p.volume_window)
        return df

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        p = self.settings
        df = dataframe
        broke = df["low"] < df["range_low"]
        # same-bar spring: the breakdown bar itself closes back above the low
        same_bar = broke & (df["close"] > df["range_low"])
        # delayed spring: the close crosses back above the level that WAS
        # broken. The reference must be the PRE-breakdown range low carried
        # forward - once the flush bar enters the rolling window, range_low
        # itself collapses to the flush low and no reclaim could ever cross it.
        broken_level = df["range_low"].where(broke).ffill(
            limit=p.reclaim_bars - 1)
        delayed = crossed_above(df["close"], broken_level)
        return (same_bar | delayed).fillna(False)

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        row = dataframe.iloc[-1]
        # shakeout depth: how far below the range low the flush reached
        depth = (float(row["range_low"]) - float(row["low"])) \
            / float(row["range_low"]) if row["range_low"] else 0.0
        climax = clip01(float(row.get("volume_ratio", 0.0)) / 3.0)
        return weighted(
            [Component("shakeout_depth", clip01(depth / 0.03), 0.5,
                       f"flushed {depth:.2%} below the range low"),
             Component("volume_climax", climax, 0.5,
                       f"volume_ratio {float(row.get('volume_ratio', 0)):.2f}")],
            reason="failed breakdown reclaimed the range low")
