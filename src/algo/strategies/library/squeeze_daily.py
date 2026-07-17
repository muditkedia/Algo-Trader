"""Weekly Volatility Squeeze - compression stores energy; the break spends it.

Thesis (Bollinger; Carter's TTM squeeze; volatility clustering generally): when
weekly Bollinger bands contract inside the weekly Keltner channel, realized
volatility is abnormally low and tends to be followed by expansion. The
expansion is DIRECTIONLESS - that ambiguity killed volexp_1h (D-026) - so the
long trigger is a daily range break out of the compressed state, and the weekly
scale makes the targeted move large enough that the 30.9 bps delivery round
trip is a small fraction of it. Chosen over the fuller Minervini VCP because it
is the same hypothesis with the fewest free parameters (roadmap C2 vs C1).
Pre-registered in research/PREREGISTRATION_BATCH1.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import (
    atr, bollinger, crossed_above, ema, weekly_asof, weekly_bars,
)
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import Component, ConfidenceScore, clip01, weighted


@dataclass(frozen=True)
class SqueezeParams:
    bb_period: int = 20             # weekly Bollinger window
    bb_std: float = 2.0
    kc_period: int = 20             # weekly Keltner window (EMA mid + ATR)
    kc_mult: float = 1.5
    trigger_lookback: int = 10      # daily range break that resolves the squeeze

    @classmethod
    def from_dict(cls, data) -> "SqueezeParams":
        return from_dict(cls, data)


class WeeklySqueezeBreakout(StrategyProfile):

    meta = StrategyMeta(
        name="squeeze_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=120,
        required_columns=("close", "squeeze_on", "trigger_high"),
        supported_regimes=("range", "bull"),
        hypothesis=("Volatility clusters: weekly Bollinger bands contracting "
                    "inside the weekly Keltner channel mark abnormal calm that "
                    "precedes expansion. An upside break of the recent daily "
                    "range while compressed resolves the stored move upward, "
                    "at a scale where the delivery cost is a small fraction "
                    "of the target."),
        expected_behaviour=("Rare: needs weeks of compression AND an upside "
                            "break - a few signals per stock per year."),
        known_failure_modes=(
            "compression predicts the move, not the sign: downside "
            "resolutions stop the position out (volexp_1h's killer, "
            "pre-registered here as the expected failure)",
            "weekly windows shrink the effective sample (~180 weeks total)",
        ),
        enabled=True,
        horizon_bars=(5, 10, 20, 30), max_hold_bars=30,
    )

    def __init__(self, settings=None) -> None:
        super().__init__(settings or SqueezeParams())

    def min_history(self) -> int:
        # bb/kc need ~21 completed weeks; ~5 sessions per week + slack
        p = self.settings
        return (max(p.bb_period, p.kc_period) + 3) * 5 + 5

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        weekly = weekly_bars(df)
        bb_up, _, bb_low = bollinger(weekly["close"], p.bb_period, p.bb_std)
        kc_mid = ema(weekly["close"], p.kc_period)
        kc_range = p.kc_mult * atr(weekly, p.kc_period)
        squeeze = ((bb_up < kc_mid + kc_range)
                   & (bb_low > kc_mid - kc_range)).astype(float)
        df["squeeze_on"] = weekly_asof(df["date"], squeeze)
        df["bb_width_w"] = weekly_asof(
            df["date"], ((bb_up - bb_low) / kc_mid).astype(float))
        df["trigger_high"] = df["high"].rolling(
            p.trigger_lookback, min_periods=p.trigger_lookback).max().shift(1)
        return df

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        compressed = dataframe["squeeze_on"] > 0.5
        breakout = crossed_above(dataframe["close"], dataframe["trigger_high"])
        return (compressed & breakout).fillna(False)

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        width = float(dataframe["bb_width_w"].iloc[-1]) \
            if "bb_width_w" in dataframe.columns else float("nan")
        # tighter compression = more stored energy (width in fraction terms)
        tightness = clip01(1.0 - width / 0.20) if width == width else 0.0
        return weighted(
            [Component("compression_tightness", tightness, 1.0,
                       f"weekly BB width {width:.1%}")],
            reason="upside range break out of a weekly squeeze")
