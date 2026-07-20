"""Donchian 55-Day Channel Breakout - the canonical trend entry, plain form.

Thesis (Donchian; Turtle System 2; Faith, *Way of the Turtle*): a close above
the prior 55-day high marks a supply/demand imbalance strong enough to clear
every seller of the past quarter; large trends begin at new highs. ONE
parameter, deliberately - the Turtle refinements (skip-filter, pyramiding) are
recorded in the library as untested variants, and the platform's own risk
engine already provides the 2N-style stop and risk-fraction sizing.
Pre-registered in research/PREREGISTRATION_BATCH1.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import atr, crossed_above
from algo.execution import atr_trail_swing
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import Component, ConfidenceScore, clip01, weighted


@dataclass(frozen=True)
class DonchianParams:
    channel: int = 55
    atr_period: int = 14        # confidence only

    @classmethod
    def from_dict(cls, data) -> "DonchianParams":
        return from_dict(cls, data)


class Donchian55Breakout(StrategyProfile):

    meta = StrategyMeta(
        name="donchian55_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=60,
        required_columns=("close", "channel_high"),
        supported_regimes=("bull",),
        hypothesis=("A close above the prior 55-day high means demand absorbed "
                    "every seller of the past quarter - an imbalance that "
                    "tends to continue. Large trends begin at new highs, not "
                    "at bottoms."),
        expected_behaviour=("Low win rate BY CONSTRUCTION (~30-40% in the "
                            "trend-following literature); the return, if any, "
                            "lives in a fat right tail of long trend rides."),
        known_failure_modes=(
            "false breakouts in range-bound markets bleed stop-outs",
            "tail-dependent payoff needs a larger sample than 3.5y may give",
            "overlaps hi52_daily at the yearly-high boundary (distinct "
            "windows: quarterly vs yearly anchor)",
        ),
        enabled=True,
        horizon_bars=(10, 20, 40, 60), max_hold_bars=60,
    )

    #: Swing execution owned by this strategy: ATR/structure stop,
    #: chandelier trail + profit locks, overnight allowed, horizon end
    #: at its declared max hold (no session square-off).
    execution = atr_trail_swing(meta.max_hold_bars)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or DonchianParams())

    def min_history(self) -> int:
        return self.settings.channel + 5

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        df["channel_high"] = df["high"].rolling(
            p.channel, min_periods=p.channel).max().shift(1)
        df["atr"] = atr(df, p.atr_period)
        return df

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        return crossed_above(dataframe["close"],
                             dataframe["channel_high"]).fillna(False)

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        row = dataframe.iloc[-1]
        # breakout margin in ATR units: a decisive clearance vs a scrape-over
        margin = ((float(row["close"]) - float(row["channel_high"]))
                  / float(row["atr"])) if row.get("atr") else 0.0
        return weighted(
            [Component("breakout_margin", clip01(margin), 1.0,
                       f"cleared the 55d high by {margin:.2f} ATR")],
            reason="close above the prior 55-day high")
