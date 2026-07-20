"""Earnings-Gap Continuation - the price-only PEAD proxy.

Thesis (Ball & Brown 1968; Bernard & Thomas 1989): prices under-react to
earnings surprises and drift in the surprise's direction for weeks. Without an
earnings feed, the information event is proxied by its footprint: a large
overnight up-gap on extreme volume that HOLDS through the session. Known proxy
contamination (non-earnings gaps) is pre-registered as a weakness.
Pre-registered in research/PREREGISTRATION_BATCH1.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import volume_ratio
from algo.execution import atr_trail_swing
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import Component, ConfidenceScore, clip01, weighted


@dataclass(frozen=True)
class EgapParams:
    min_gap: float = 0.03           # 3% overnight gap
    min_volume_ratio: float = 3.0   # vs the 20-day mean
    volume_window: int = 20

    @classmethod
    def from_dict(cls, data) -> "EgapParams":
        return from_dict(cls, data)


class EarningsGapContinuation(StrategyProfile):

    meta = StrategyMeta(
        name="egap_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=25,
        required_columns=("close", "gap_pct", "volume_ratio"),
        supported_regimes=("bull", "range"),
        hypothesis=("A large overnight up-gap on extreme volume marks an "
                    "information event (usually earnings); under-reaction "
                    "drifts the price further for weeks after (PEAD). Entering "
                    "at the gap day's close - only if the gap HELD - buys the "
                    "drift, not the gap itself."),
        expected_behaviour=("Rare event signal: a handful of firings per stock "
                            "per year, clustered around results seasons."),
        known_failure_modes=(
            "proxy contamination: index events, blocks and news gaps that are "
            "not earnings and carry no drift",
            "the surprise's sign vs expectation is unobservable without an "
            "earnings feed - some 'up gaps' are already-priced relief",
            "gap-day close entry concedes the first day of any drift",
        ),
        enabled=True,
        horizon_bars=(5, 10, 20, 40), max_hold_bars=40,
    )

    #: Swing execution owned by this strategy: ATR/structure stop,
    #: chandelier trail + profit locks, overnight allowed, horizon end
    #: at its declared max hold (no session square-off).
    execution = atr_trail_swing(meta.max_hold_bars)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or EgapParams())

    def min_history(self) -> int:
        return self.settings.volume_window + 5

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        df["gap_pct"] = df["open"] / df["close"].shift(1) - 1.0
        df["volume_ratio"] = volume_ratio(df["volume"], p.volume_window)
        return df

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        p = self.settings
        df = dataframe
        gapped = df["gap_pct"] >= p.min_gap
        heavy = df["volume_ratio"] >= p.min_volume_ratio
        held = df["close"] >= df["open"]        # gap not faded intraday
        # naturally edge-triggered: the gap exists only on its own bar
        return (gapped & heavy & held).fillna(False)

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        row = dataframe.iloc[-1]
        gap = float(row["gap_pct"])
        vol = float(row["volume_ratio"])
        gap_size = clip01((gap - self.settings.min_gap) / 0.05)
        volume = clip01(vol / (2.0 * self.settings.min_volume_ratio))
        return weighted(
            [Component("gap_size", gap_size, 0.5, f"gap {gap:+.1%}"),
             Component("volume_extremity", volume, 0.5,
                       f"volume {vol:.1f}x its 20d mean")],
            reason="held up-gap on extreme volume (information-event proxy)")
