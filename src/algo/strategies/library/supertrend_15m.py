"""Supertrend Continuation - ride the flip of the Supertrend state.

Thesis (Olivier Seban's Supertrend; among the most widely used intraday
indicators in Indian retail/discretionary practice, canonical parameters
(10, 3)): the Supertrend line is a volatility-adjusted trailing level; when
price closes through the upper band the trend state flips bullish, and the
canonical trade is to be long WITH the state, exiting when price falls back
through the (rising) line. Distinct from every existing strategy: a
trend-STATE follower - no breakout level, no pullback, no session anchor.

Canonical rules implemented (no optimisation): Supertrend(10, 3.0) on 15m
bars with Wilder ATR (the standard formulation); entry on the bar whose
close flips the state bullish; initial stop AT the line; exit by trailing
the line itself (the engine's `column` trail - stop ratchets with the line,
so the position exits intrabar exactly where the state would flip); no
profit target (ride the state, the canonical usage); square-off at session
end; never overnight.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import atr, supertrend, volume_ratio
from algo.execution import ExecutionSpec
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import Component, ConfidenceScore, clip01, \
    weighted


@dataclass(frozen=True)
class SupertrendParams:
    period: int = 10
    multiplier: float = 3.0
    atr_period: int = 14
    volume_window: int = 20

    @classmethod
    def from_dict(cls, data) -> "SupertrendParams":
        return from_dict(cls, data)


class SupertrendContinuation(StrategyProfile):

    meta = StrategyMeta(
        name="supertrend_15m", version="1.0",
        blocked_by_pre_partial_groups=("ema_compression",),
        direction=Direction.LONG, holding_scope=HoldingScope.INTRADAY,
        timeframe="15m", min_bars=40,
        required_columns=("close", "st_line", "st_dir", "atr",
                          "volume_ratio"),
        supported_regimes=("bull", "range"),
        hypothesis=("A bullish Supertrend(10,3) flip marks a volatility-"
                    "adjusted trend change; staying long until price falls "
                    "back through the rising line captures the trend leg."),
        expected_behaviour=("A handful of flips per stock per week; losses "
                            "are quick re-flips, winners ride for hours."),
        known_failure_modes=(
            "whipsaw in ranges: alternating flips bleed repeated small stops",
            "late flips: the line trails by construction, so entries concede "
            "part of every move",
            "session boundaries: an overnight gap can re-flip the state at "
            "the open (position is already flat by square-off rule)",
        ),
        enabled=True,
    )

    #: Supertrend's OWN execution (the canonical usage): initial stop at the
    #: line, then TRAIL THE LINE itself; no target (ride the state);
    #: square-off at session end; never overnight.
    execution = ExecutionSpec(stop_kind="column", stop_col="st_line",
                              hard_stop_pct=None, target_kind="none",
                              trail="column", trail_col="st_line",
                              intraday=True, allow_overnight=False,
                              max_hold_bars=None)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or SupertrendParams())

    def min_history(self) -> int:
        p = self.settings
        return max(p.period * 3, p.volume_window + 5, self.meta.min_bars)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        line, direction = supertrend(df, p.period, p.multiplier)
        df["st_line"] = line
        df["st_dir"] = direction
        df["atr"] = atr(df, p.atr_period)
        df["volume_ratio"] = volume_ratio(df["volume"], p.volume_window)
        return df

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        df = dataframe
        # the flip bar: direction goes non-bullish -> bullish
        flipped = (df["st_dir"] > 0) & (df["st_dir"].shift(1) <= 0)
        return flipped.fillna(False)

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        row = dataframe.iloc[-1]
        # flip decisiveness: distance above the fresh line, in ATR units
        margin = ((float(row["close"]) - float(row["st_line"]))
                  / float(row["atr"])) if row["atr"] else 0.0
        volume = clip01(float(row["volume_ratio"]) / 2.0)
        return weighted(
            [Component("flip_margin", clip01(margin / 1.0), 0.6,
                       f"close {margin:.2f} ATR above the flipped line"),
             Component("volume", volume, 0.4,
                       f"volume_ratio {float(row['volume_ratio']):.2f}")],
            reason="supertrend state flipped bullish")
