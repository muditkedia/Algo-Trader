"""Turn-of-month - buy into the month-boundary demand window.

Thesis (Ariel 1987; Lakonishok-Smidt 1988): equity returns concentrate around
month boundaries. India has an unusually strong, DATED version of the mechanism
- large monthly systematic-investment (SIP) inflows land at month-end/start. A
specific, checkable demand story, not a mined calendar quirk. A per-symbol
seasonality hypothesis with no batch-1 analogue. Pre-registered in
research/PREREGISTRATION_BATCH2.md (#9).

Causality: the window is defined purely on the CALENDAR (business days to
month-end), which is known in advance - no market data from the future enters
the signal.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import ConfidenceScore, weighted, Component


@dataclass(frozen=True)
class TomParams:
    days_before_end: int = 2        # enter this many business days before month-end

    @classmethod
    def from_dict(cls, data) -> "TomParams":
        return from_dict(cls, data)


class TurnOfMonth(StrategyProfile):

    meta = StrategyMeta(
        name="tom_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=25,
        required_columns=("close", "tom_window"),
        supported_regimes=("bull", "range", "bear"),
        hypothesis=("Returns concentrate around month boundaries (India: dated "
                    "monthly SIP inflows). Enter ~2 business days before "
                    "month-end and hold through the turn."),
        expected_behaviour="~One entry per stock per month, held ~a week.",
        known_failure_modes=(
            "calendar effects are the classic data-mining trap; many decay",
            "~130 monthly events over 11y is a thin independent sample",
            "the effect, if real, is small vs a 30.9 bps delivery round trip",
        ),
        enabled=True,
        horizon_bars=(3, 5, 7), max_hold_bars=7,
    )

    def __init__(self, settings=None) -> None:
        super().__init__(settings or TomParams())

    def min_history(self) -> int:
        return 25

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        dates = out["date"].dt.tz_localize(None).dt.normalize()
        bme = dates + pd.offsets.BMonthEnd(0)          # last business day of month
        # business days from each date to its month-end (0 = the last b-day)
        d = dates.to_numpy("datetime64[D]")
        e = bme.to_numpy("datetime64[D]")
        days_to_end = np.busday_count(d, e)            # >=0 within the month
        out["tom_window"] = ((days_to_end >= 0)
                             & (days_to_end <= self.settings.days_before_end)
                             ).astype(float)
        return out

    def entry_signal(self, df: pd.DataFrame) -> pd.Series:
        if self.missing_columns(df) or len(df) < self.min_history():
            return self.no_signal(df)
        w = df["tom_window"] > 0.5
        # edge-trigger: fire on the first bar entering the month-end window
        return (w & ~w.shift(1, fill_value=False)).fillna(False)

    def confidence(self, df: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(df) or df.empty:
            return ConfidenceScore.zero("missing columns")
        return weighted(
            [Component("turn_of_month", 0.5, 1.0, "month-end demand window")],
            reason="entered the turn-of-month window")
