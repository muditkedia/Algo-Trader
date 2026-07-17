"""52-Week-High Proximity - the anchor traders watch, cleared.

Thesis (George & Hwang 2004, JF): investors anchor on the 52-week high and
under-react to good news near it; when price re-enters the near-high band the
anchor's resistance is being overcome and the underlying news continues to be
priced in. Deliberately parameter-light: one lookback, one band.
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
class Hi52Params:
    lookback: int = 252         # the 52-week window
    band: float = 0.95          # "near the high" = within 5%
    volume_window: int = 20     # confidence only, not a gate

    @classmethod
    def from_dict(cls, data) -> "Hi52Params":
        return from_dict(cls, data)


class High52WeekProximity(StrategyProfile):

    meta = StrategyMeta(
        name="hi52_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=260,
        required_columns=("close", "hi52_ratio"),
        supported_regimes=("bull",),
        hypothesis=("Traders anchor on the 52-week high; near it, good news is "
                    "under-priced because the anchor 'looks expensive'. "
                    "Re-entering the near-high band marks the anchor being "
                    "overcome and the move continuing."),
        expected_behaviour=("Fires when price crosses INTO the top-5% band of "
                            "its trailing 52-week high - once per excursion, "
                            "a few times per stock per year."),
        known_failure_modes=(
            "market tops: everything sits near its high just before a fall",
            "concentrates into whatever already ran the hardest",
            "the published effect is a cross-sectional RANK; this threshold "
            "form is a weaker per-symbol expression (pre-registered)",
        ),
        enabled=True,
        horizon_bars=(10, 20, 40, 60), max_hold_bars=60,
    )

    def __init__(self, settings=None) -> None:
        super().__init__(settings or Hi52Params())

    def min_history(self) -> int:
        return self.settings.lookback + 5

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        # prior 252-day high, excluding today (shift 1) - today's close can
        # therefore legitimately exceed a ratio of 1.0 on a fresh high.
        prior_high = df["high"].rolling(p.lookback, min_periods=p.lookback)\
            .max().shift(1)
        df["hi52_ratio"] = df["close"] / prior_high
        df["volume_ratio"] = volume_ratio(df["volume"], p.volume_window)
        return df

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        band = pd.Series(self.settings.band, index=dataframe.index)
        return crossed_above(dataframe["hi52_ratio"], band).fillna(False)

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        row = dataframe.iloc[-1]
        ratio = float(row["hi52_ratio"])
        # depth of the band entry (1.0 = at the actual high)
        proximity = clip01((ratio - self.settings.band)
                           / (1.0 - self.settings.band))
        participation = clip01(float(row.get("volume_ratio", 0.0)) / 2.0)
        return weighted(
            [Component("anchor_proximity", proximity, 0.6,
                       f"close at {ratio:.1%} of the 52-week high"),
             Component("volume", participation, 0.4,
                       f"volume_ratio {float(row.get('volume_ratio', 0)):.2f}")],
            reason="crossed into the 52-week-high band")
