"""Elder Triple Screen - the weekly tide, the daily wave, the strength trigger.

Thesis (Elder 1993): trade only in the direction of the higher-timeframe trend
(screen 1, the weekly tide), enter on a lower-timeframe pullback against it
(screen 2, the daily wave), and only when strength returns (screen 3, the
trigger) - buying temporary weakness inside an established uptrend. Elder's
money-management rules are superseded by the platform risk engine (D-006).

Lookahead rule (pre-registered): weekly values join to a daily bar only when
the week completed on or before that bar's date - `weekly_asof` implements
exactly that, and the truncation test in tests/test_batch1_strategies.py
proves signals are unchanged when the frame is cut mid-week.
Pre-registered in research/PREREGISTRATION_BATCH1.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import crossed_above, ema, weekly_asof, weekly_bars
from algo.execution import atr_trail_swing
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import Component, ConfidenceScore, clip01, weighted


@dataclass(frozen=True)
class TripleScreenParams:
    weekly_ema: int = 13        # Elder's weekly trend EMA
    fi_period: int = 2          # Force Index smoothing (the "wave")

    @classmethod
    def from_dict(cls, data) -> "TripleScreenParams":
        return from_dict(cls, data)


class ElderTripleScreen(StrategyProfile):

    meta = StrategyMeta(
        name="triple_screen_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=80,
        required_columns=("close", "high", "tide", "fi2"),
        supported_regimes=("bull",),
        hypothesis=("The weekly trend (tide) dominates the daily noise (wave): "
                    "a daily pullback inside a rising weekly trend is "
                    "temporary weakness, and the return of strength (a break "
                    "of the prior day's high) marks its end - a with-trend "
                    "entry at a better price than chasing strength."),
        expected_behaviour=("The most frequent strategy in the batch: every "
                            "pullback-and-resume inside a weekly uptrend "
                            "fires once."),
        known_failure_modes=(
            "pullbacks that are the start of the trend's end, not weakness",
            "the platform's only measured pullback strategy (pullback_15m) "
            "was its worst FAIL - the defence is scale, not the pattern",
            "trade-book evidence only; thin peer-reviewed support",
        ),
        enabled=True,
        horizon_bars=(3, 5, 10, 20), max_hold_bars=20,
    )

    #: Swing execution owned by this strategy: ATR/structure stop,
    #: chandelier trail + profit locks, overnight allowed, horizon end
    #: at its declared max hold (no session square-off).
    execution = atr_trail_swing(meta.max_hold_bars)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or TripleScreenParams())

    def min_history(self) -> int:
        # ~15 completed weeks for a meaningful weekly EMA slope
        return (self.settings.weekly_ema + 2) * 5 + 5

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        weekly = weekly_bars(df)
        tide = (ema(weekly["close"], p.weekly_ema).diff() > 0).astype(float)
        df["tide"] = weekly_asof(df["date"], tide)
        force = (df["close"] - df["close"].shift(1)) * df["volume"]
        df["fi2"] = ema(force, p.fi_period)
        return df

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        df = dataframe
        tide_up = df["tide"] > 0.5                       # screen 1
        pulled_back = df["fi2"].shift(1) < 0             # screen 2 (yesterday)
        trigger = crossed_above(df["close"], df["high"].shift(1))  # screen 3
        return (tide_up & pulled_back & trigger).fillna(False)

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        df = dataframe
        row = df.iloc[-1]
        # pullback depth: how negative the smoothed Force Index got yesterday,
        # scaled by its own recent magnitude
        scale = float(df["fi2"].abs().tail(50).mean()) or 1.0
        prior_fi = float(df["fi2"].iloc[-2]) if len(df) > 1 else 0.0
        depth = clip01(-prior_fi / (2.0 * scale))
        strength = clip01((float(row["close"]) / float(df["high"].iloc[-2]) - 1.0)
                          / 0.02) if len(df) > 1 else 0.0
        return weighted(
            [Component("pullback_depth", depth, 0.5,
                       f"FI2 was {prior_fi:.3g} vs typical {scale:.3g}"),
             Component("trigger_strength", strength, 0.5,
                       "break above the prior day's high")],
            reason="pullback ended inside a rising weekly tide")
