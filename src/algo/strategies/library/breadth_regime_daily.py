"""Breadth-regime momentum - breakouts taken only when the market is broad.

Thesis: trend/momentum works when market participation is broad; gating entries
on breadth (fraction of the universe above its 200-day MA > 50%) avoids whipsaws
in narrow, top-heavy markets. A REGIME-DEPENDENT strategy using a cross-sectional
AGGREGATE (breadth) as the filter - a distinct construct (market-breadth family),
not a single-stock signal. Pre-registered in
research/PREREGISTRATION_BATCH2.md (#10).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import Component, ConfidenceScore, clip01, weighted
from algo.core.indicators import crossed_above
from algo.strategies.cross_section import breadth, scatter_series


@dataclass(frozen=True)
class BreadthParams:
    breadth_ma: int = 200
    breadth_threshold: float = 0.50
    breakout: int = 50

    @classmethod
    def from_dict(cls, data) -> "BreadthParams":
        return from_dict(cls, data)


class BreadthRegimeMomentum(StrategyProfile):
    cross_sectional = True

    meta = StrategyMeta(
        name="breadth_regime_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=210,
        required_columns=("close", "breakout_high", "breadth", "above_ma"),
        supported_regimes=("bull",),
        hypothesis=("A 50-day breakout, taken ONLY when market breadth (% of the "
                    "universe above its 200-day MA) exceeds 50%. Regime-gated "
                    "momentum using a cross-sectional breadth aggregate."),
        expected_behaviour="Trades in broad bull phases; silent in narrow ones.",
        known_failure_modes=(
            "breadth is highly autocorrelated - few independent regime periods",
            "breakout leg alone is close to donchian (rejected); the breadth "
            "gate is the differentiator, and may simply reduce sample size",
            "long-only",
        ),
        enabled=True,
        horizon_bars=(21, 42, 63), max_hold_bars=63,
    )

    def __init__(self, settings=None) -> None:
        super().__init__(settings or BreadthParams())

    def min_history(self) -> int:
        return self.settings.breadth_ma + 10

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        out = df.copy()
        out["breakout_high"] = out["high"].rolling(
            p.breakout, min_periods=p.breakout).max().shift(1)
        ma = out["close"].rolling(p.breadth_ma, min_periods=p.breadth_ma).mean()
        out["above_ma"] = (out["close"] > ma).astype(float)
        return out

    def prepare_cross_section(self, frames):
        b = breadth(frames, "above_ma")                # % of universe above 200MA
        scatter_series(frames, b, "breadth")
        return frames

    def entry_signal(self, df: pd.DataFrame) -> pd.Series:
        if self.missing_columns(df) or len(df) < self.min_history():
            return self.no_signal(df)
        broad = df["breadth"] > self.settings.breadth_threshold
        breakout = crossed_above(df["close"], df["breakout_high"])
        return (broad & breakout).fillna(False)

    def confidence(self, df: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(df) or df.empty:
            return ConfidenceScore.zero("missing columns")
        br = float(df["breadth"].iloc[-1]) if df["breadth"].iloc[-1] == \
            df["breadth"].iloc[-1] else 0.0
        return weighted(
            [Component("breadth", clip01((br - 0.5) / 0.4), 1.0,
                       f"{br:.0%} of the universe above its 200-day MA")],
            reason="breakout confirmed by broad market participation")
