"""First Pullback After Breakout - the highest-quality continuation entry.

Thesis: after an intraday breakout (close above the opening-range high), the
FIRST pullback that HOLDS the breakout level (a higher low, staying above the OR
high) confirms demand; the resume above that pullback's high is a defined-risk
continuation entry. Only the FIRST pullback per session is taken.

Distinct from pullback_15m (any pullback to the fast EMA in an uptrend): this
requires a prior BREAKOUT of the opening range and takes only the first
qualifying pullback-and-resume. Pre-registered in
research/INTRADAY_PRODUCTION_BATCH1.md (#5).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import atr, crossed_above, opening_range, volume_ratio
from algo.execution import structural_intraday
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import Component, ConfidenceScore, clip01, weighted


@dataclass(frozen=True)
class FirstPullbackParams:
    range_minutes: int = 15
    atr_period: int = 14
    volume_window: int = 20

    @classmethod
    def from_dict(cls, data) -> "FirstPullbackParams":
        return from_dict(cls, data)


class FirstPullbackAfterBreakout(StrategyProfile):

    meta = StrategyMeta(
        name="first_pullback_15m", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.INTRADAY,
        timeframe="15m", min_bars=30,
        required_columns=("close", "high", "or_high", "pullback_high",
                          "after_first_pb", "atr", "volume_ratio"),
        supported_regimes=("bull", "range"),
        hypothesis=("After an opening-range breakout, the first pullback that "
                    "holds above the breakout level confirms demand; the resume "
                    "above that pullback's high is a defined-risk continuation."),
        expected_behaviour=("At most one long per stock per session, only after "
                            "a breakout AND a holding first pullback."),
        known_failure_modes=(
            "the first pullback breaks the breakout level (failed breakout)",
            "no pullback: an open-drive that never offers the entry",
            "late-session resume with no time before square-off",
        ),
        enabled=True,
    )

    #: This strategy's OWN execution (the published defined-risk form,
    #: research/INTRADAY_PRODUCTION_BATCH1.md section 5): stop below the
    #: first pullback's low, 2R target, no trail, session square-off,
    #: never overnight.
    execution = structural_intraday(stop_col="pullback_low", target_kind="r",
                                    target_r=2.0)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or FirstPullbackParams())

    def min_history(self) -> int:
        return max(self.settings.volume_window + 5, self.meta.min_bars)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        day = df["date"].dt.normalize()
        or_high, _, after = opening_range(df, p.range_minutes)
        df["or_high"] = or_high
        df["after_range"] = after

        broke = crossed_above(df["close"], or_high) & after
        # broken as of the PRIOR bar, within the session (no cross-session leak)
        has_broken_prev = broke.groupby(day).cummax().groupby(day).shift(
            1, fill_value=False)
        # a pullback: a down bar AFTER the breakout that HOLDS above the OR high
        down_bar = df["close"] < df["close"].shift(1)
        holds = df["low"] >= or_high
        is_pullback = has_broken_prev & down_bar & holds & after
        # keep only the FIRST pullback of each session
        first_pb = is_pullback & (is_pullback.groupby(day).cumsum() == 1)
        # the first pullback's high, carried forward; and the post-pullback flag
        df["pullback_high"] = df["high"].where(first_pb).groupby(day).ffill()
        # ... and its LOW - this strategy's published stop level
        df["pullback_low"] = df["low"].where(first_pb).groupby(day).ffill()
        df["after_first_pb"] = first_pb.groupby(day).cummax().groupby(day).shift(
            1, fill_value=False).astype(float)
        df["atr"] = atr(df, p.atr_period)
        df["volume_ratio"] = volume_ratio(df["volume"], p.volume_window)
        return df

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        df = dataframe
        day = df["date"].dt.normalize()
        resume = crossed_above(df["close"], df["pullback_high"])
        still_above = df["close"] > df["or_high"]
        raw = (df["after_first_pb"] > 0.5) & resume & still_above
        # one entry per session (the first resume after the first pullback)
        return (raw & (raw.groupby(day).cumsum() == 1)).fillna(False)

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        row = dataframe.iloc[-1]
        # resume strength: how far above the pullback high the close resumed
        margin = ((float(row["close"]) - float(row["pullback_high"]))
                  / float(row["atr"])) if row.get("atr") and pd.notna(
            row.get("pullback_high")) else 0.0
        volume = clip01(float(row["volume_ratio"]) / 2.0)
        return weighted(
            [Component("resume_strength", clip01(margin / 0.5), 0.6,
                       "close above the first-pullback high"),
             Component("volume", volume, 0.4,
                       f"volume_ratio {float(row['volume_ratio']):.2f}")],
            reason="resume after the first pullback held the breakout level")
