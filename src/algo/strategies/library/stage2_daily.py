"""Weinstein Stage 2 - the Stage 1->2 breakout into the markup phase.

Thesis (Weinstein 1988): stocks move through four stages; buying the Stage 1->2
transition - a breakout from a base ABOVE a rising 30-week moving average on
volume expansion - catches the markup phase. A multi-condition STRUCTURAL regime
thesis (base + rising long MA + volume), distinct from a single breakout
(donchian, rejected) or a pullback. Per-symbol. Pre-registered in
research/PREREGISTRATION_BATCH2.md (#11).
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
class Stage2Params:
    ma_period: int = 150            # ~30 weeks
    high_period: int = 150          # base breakout lookback
    slope_lookback: int = 20        # MA must be rising over this
    volume_window: int = 50
    volume_mult: float = 1.5

    @classmethod
    def from_dict(cls, data) -> "Stage2Params":
        return from_dict(cls, data)


class WeinsteinStage2(StrategyProfile):

    meta = StrategyMeta(
        name="stage2_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=170,
        required_columns=("close", "ma30w", "ma_rising", "base_high",
                          "volume_ratio"),
        supported_regimes=("bull",),
        hypothesis=("A close breaking a 150-day base high, above a RISING "
                    "30-week (150-day) MA, on >1.5x average volume = the Stage "
                    "1->2 transition into markup. Structural, multi-condition."),
        expected_behaviour="A few Stage-2 breakouts per stock per cycle.",
        known_failure_modes=(
            "'base' and 'stage' are discretionary - the encoding may not match "
            "Weinstein's intent",
            "the breakout leg alone resembles donchian (rejected); the MA-slope "
            "+ volume filters are the differentiators",
            "false breakouts in choppy markets",
        ),
        enabled=True,
        horizon_bars=(21, 63, 126), max_hold_bars=126,
    )

    def __init__(self, settings=None) -> None:
        super().__init__(settings or Stage2Params())

    def min_history(self) -> int:
        p = self.settings
        return max(p.ma_period + p.slope_lookback, p.high_period) + 10

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        out = df.copy()
        out["ma30w"] = out["close"].rolling(
            p.ma_period, min_periods=p.ma_period).mean()
        out["ma_rising"] = (out["ma30w"] > out["ma30w"].shift(p.slope_lookback)
                            ).astype(float)
        out["base_high"] = out["high"].rolling(
            p.high_period, min_periods=p.high_period).max().shift(1)
        out["volume_ratio"] = volume_ratio(out["volume"], p.volume_window)
        return out

    def entry_signal(self, df: pd.DataFrame) -> pd.Series:
        if self.missing_columns(df) or len(df) < self.min_history():
            return self.no_signal(df)
        p = self.settings
        breakout = crossed_above(df["close"], df["base_high"])
        rising = df["ma_rising"] > 0.5
        above = df["close"] > df["ma30w"]
        volume = df["volume_ratio"] >= p.volume_mult
        return (breakout & rising & above & volume).fillna(False)

    def confidence(self, df: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(df) or df.empty:
            return ConfidenceScore.zero("missing columns")
        row = df.iloc[-1]
        vol = float(row["volume_ratio"]) if row["volume_ratio"] == \
            row["volume_ratio"] else 0.0
        return weighted(
            [Component("volume_thrust", clip01(vol / 3.0), 1.0,
                       f"volume {vol:.1f}x its 50-day average")],
            reason="stage 1->2 breakout above a rising 30-week MA on volume")
