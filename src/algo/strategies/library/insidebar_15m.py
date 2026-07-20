"""Inside-Bar Breakout - price-action compression, resolved upward.

Thesis (price-action canon: the inside bar is among the oldest documented
bar patterns - Crabel's ID/NR lineage, Al Brooks' "ii"/inside-bar breakout
practice, and every modern price-action curriculum): an inside bar (its full
range within the prior "mother" bar's range) marks a one-bar equilibrium;
the break of the mother bar's extreme resolves that equilibrium and tends to
follow through. Distinct from `volexp_1h` (indicator-based multi-bar squeeze)
and `nr7_intraday_15m` (a DAILY compression gate) - this is the pure two-bar
intraday pattern.

Canonical rules implemented (no optimisation): mother bar and inside bar in
the SAME session (a pattern spanning the overnight gap is meaningless); the
inside bar's range strictly inside the mother's; entry when a bar closes
above the mother bar's high while the pattern is still fresh (within 2 bars
of the inside bar); stop below the inside bar's low (the tight, most-taught
placement); 2R target; square-off; never overnight.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import atr, crossed_above, volume_ratio
from algo.execution import structural_intraday
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import Component, ConfidenceScore, clip01, \
    weighted


@dataclass(frozen=True)
class InsideBarParams:
    #: the breakout must come within this many bars of the inside bar
    breakout_lookback: int = 2
    atr_period: int = 14
    volume_window: int = 20

    @classmethod
    def from_dict(cls, data) -> "InsideBarParams":
        return from_dict(cls, data)


class InsideBarBreakout(StrategyProfile):

    meta = StrategyMeta(
        name="insidebar_15m", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.INTRADAY,
        timeframe="15m", min_bars=30,
        required_columns=("close", "high", "low", "mother_high", "ib_low",
                          "pattern_fresh", "atr", "volume_ratio"),
        supported_regimes=("bull", "range"),
        hypothesis=("An inside bar is a one-bar equilibrium; the break of its "
                    "mother bar's high resolves the balance upward and tends "
                    "to follow through intraday."),
        expected_behaviour=("A few signals per stock per week; fires only "
                            "while a pattern is fresh (within 2 bars)."),
        known_failure_modes=(
            "midday equilibria that break and immediately mean-revert",
            "inside bars inside wide mother bars - the 'break' is deep in "
            "old range",
            "stacked inside bars (iii) where the first break is the trap",
        ),
        enabled=True,
    )

    #: Inside-Bar's OWN execution: stop below the inside bar's low (the
    #: tight canonical placement), 2R target, no trail, session square-off.
    execution = structural_intraday(stop_col="ib_low", target_kind="r",
                                    target_r=2.0)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or InsideBarParams())

    def min_history(self) -> int:
        return max(self.settings.volume_window + 5, self.meta.min_bars)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        day = df["date"].dt.normalize()
        same_session = day.eq(day.shift(1))
        inside = (same_session
                  & (df["high"] < df["high"].shift(1))
                  & (df["low"] > df["low"].shift(1)))
        # pattern levels, carried while fresh (inside bar at t: mother is t-1)
        mother_high = df["high"].shift(1).where(inside)
        ib_low = df["low"].where(inside)
        df["mother_high"] = mother_high.groupby(day).ffill(
            limit=p.breakout_lookback)
        df["ib_low"] = ib_low.groupby(day).ffill(limit=p.breakout_lookback)
        df["pattern_fresh"] = df["mother_high"].notna().astype(float)
        df["atr"] = atr(df, p.atr_period)
        df["volume_ratio"] = volume_ratio(df["volume"], p.volume_window)
        return df

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        df = dataframe
        fresh = df["pattern_fresh"] > 0.5
        breakout = crossed_above(df["close"], df["mother_high"])
        return (fresh & breakout).fillna(False)

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        row = dataframe.iloc[-1]
        # compression quality: pattern risk (mother high - inside low) in ATR
        risk = (float(row["mother_high"]) - float(row["ib_low"])) \
            if pd.notna(row["mother_high"]) and pd.notna(row["ib_low"]) else 0.0
        tightness = clip01(1.0 - (risk / float(row["atr"]) if row["atr"]
                                  else 1.0) / 2.0)
        volume = clip01(float(row["volume_ratio"]) / 2.0)
        return weighted(
            [Component("pattern_tightness", tightness, 0.5,
                       f"pattern risk {risk:.2f} vs ATR"),
             Component("volume", volume, 0.5,
                       f"volume_ratio {float(row['volume_ratio']):.2f}")],
            reason="inside-bar equilibrium resolved above the mother high")
