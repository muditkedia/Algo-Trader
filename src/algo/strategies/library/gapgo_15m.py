"""Gap-and-Go - overnight-gap momentum continuation, traded off the open.

Thesis (momentum day-trading canon: the "gap and go" is a staple of US
momentum practice - Warrior/SMB curricula - and of Indian opening-session
trading): a substantial overnight up-gap marks a demand imbalance; when the
FIRST bar of the session holds the gap (does not fade below the open) and
price then breaks that first bar's high, the imbalance is continuing and the
session tends to extend. Entirely distinct from ORB (level = FIRST BAR's
high on a gap day, not an opening-range level on any day) and from the daily
`egap_daily` (a multi-day PEAD proxy; this is a same-day momentum trade).

Canonical rules implemented (no optimisation): gap up >= 2% vs the prior
session close; first bar holds the gap (its low stays above the prior close
and it closes at/above its open); entry when a bar in the OPENING PHASE
(first ~90 minutes - gap-and-go is an opening-momentum play by definition,
every source frames it so) closes above the first bar's high; stop below the
first bar's low; 2R target; square-off; never overnight.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import atr, crossed_above, prior_session_ohlc, \
    volume_ratio
from algo.execution import structural_intraday
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import Component, ConfidenceScore, clip01, \
    weighted


@dataclass(frozen=True)
class GapGoParams:
    #: minimum overnight gap (open vs prior session close)
    min_gap: float = 0.02
    #: entry allowed only this many bars into the session (opening phase)
    entry_window_bars: int = 6
    atr_period: int = 14
    volume_window: int = 20

    @classmethod
    def from_dict(cls, data) -> "GapGoParams":
        return from_dict(cls, data)


class GapAndGo(StrategyProfile):

    meta = StrategyMeta(
        name="gapgo_15m", version="1.0",
        exclusive_group="opening_breakout",
        direction=Direction.LONG, holding_scope=HoldingScope.INTRADAY,
        timeframe="15m", min_bars=30,
        required_columns=("close", "high", "low", "gap_pct", "first_high",
                          "first_low", "gap_held", "session_bar", "atr",
                          "volume_ratio"),
        supported_regimes=("bull", "range"),
        hypothesis=("A >=2% overnight up-gap whose first bar holds (no fade "
                    "below the open) marks a continuing demand imbalance; "
                    "breaking the first bar's high in the opening phase rides "
                    "the continuation."),
        expected_behaviour=("Rare, event-driven: only gap mornings; at most "
                            "one signal per stock per session, always early."),
        known_failure_modes=(
            "exhaustion gaps that reverse after a marginal new high (trap)",
            "gap-and-fade days where the first bar holds but the second leg "
            "never comes",
            "large gaps into prior supply that cap the move immediately",
        ),
        enabled=True,
    )

    #: Gap-and-Go's OWN execution (the published defined-risk form): stop
    #: below the first bar's low, 2R target, no trail, session square-off.
    execution = structural_intraday(stop_col="first_low", target_kind="r",
                                    target_r=2.0)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or GapGoParams())

    def min_history(self) -> int:
        return max(self.settings.volume_window + 5, self.meta.min_bars)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        day = df["date"].dt.normalize()
        _, _, _, prev_close = prior_session_ohlc(df)
        first_open = df.groupby(day)["open"].transform("first")
        df["gap_pct"] = first_open / prev_close - 1.0
        df["first_high"] = df.groupby(day)["high"].transform("first")
        df["first_low"] = df.groupby(day)["low"].transform("first")
        first_close = df.groupby(day)["close"].transform("first")
        # the first bar HOLDS the gap: no fade below the prior close and a
        # non-bearish first bar (close >= open)
        df["gap_held"] = ((df["first_low"] > prev_close)
                          & (first_close >= first_open)).astype(float)
        df["session_bar"] = df.groupby(day).cumcount()
        df["atr"] = atr(df, p.atr_period)
        df["volume_ratio"] = volume_ratio(df["volume"], p.volume_window)
        return df

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        p = self.settings
        df = dataframe
        gapped = df["gap_pct"] >= p.min_gap
        held = df["gap_held"] > 0.5
        # opening phase only; bar 0 itself cannot break its own high
        in_window = ((df["session_bar"] >= 1)
                     & (df["session_bar"] <= p.entry_window_bars))
        breakout = crossed_above(df["close"], df["first_high"])
        raw = (gapped & held & in_window & breakout).fillna(False)
        # one shot per gap morning (the canonical form and this strategy's own
        # documented behaviour); re-crosses of the first-bar high re-fired in
        # 11% of signal sessions before this cap (verification-phase finding)
        day = df["date"].dt.normalize()
        return (raw & (raw.groupby(day).cumsum() == 1)).fillna(False)

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        row = dataframe.iloc[-1]
        gap = float(row["gap_pct"]) if pd.notna(row["gap_pct"]) else 0.0
        gap_quality = clip01((gap - self.settings.min_gap) / 0.03)
        volume = clip01(float(row["volume_ratio"]) / 2.0)
        return weighted(
            [Component("gap_size", gap_quality, 0.5,
                       f"overnight gap {gap:+.1%}"),
             Component("volume", volume, 0.5,
                       f"volume_ratio {float(row['volume_ratio']):.2f}")],
            reason="gap held and the first bar's high broke in the opening "
                   "phase")
