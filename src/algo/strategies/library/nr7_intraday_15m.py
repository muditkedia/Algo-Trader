"""NR7 Intraday - trade the range expansion out of a 7-day compression, same day.

Thesis (Toby Crabel, *Day Trading with Short Term Price Patterns and Opening
Range Breakout* - the original, most-cited narrow-range research; NR7
day-trading is standard curriculum across breakout literature): a day whose
range is the narrowest of the last seven marks multi-day volatility
exhaustion; the NEXT session's break of that narrow day's high tends to
begin a directional expansion, tradeable the same day. Distinct from
`orb_5m` (the trigger is the PRIOR DAY's high
after a daily compression, not an opening-range level on any day) - this is
the intraday execution of the Crabel pattern the target list called missing.

Canonical rules implemented (no optimisation, deliberately price-only like
Crabel's studies): yesterday was NR7 (narrowest daily range of the trailing
7 sessions, computed from completed prior sessions only); entry when a bar
closes above yesterday's high; stop below yesterday's LOW (the compression
range itself is the risk - narrow by construction); no profit target (the
premise IS the expansion day - ride it); square-off at the session's last
bar; never overnight.
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
class Nr7IntradayParams:
    #: the narrow day must have the smallest range of this many sessions
    nr_lookback: int = 7
    atr_period: int = 14
    volume_window: int = 20

    @classmethod
    def from_dict(cls, data) -> "Nr7IntradayParams":
        return from_dict(cls, data)


class Nr7Intraday(StrategyProfile):

    meta = StrategyMeta(
        name="nr7_intraday_15m", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.INTRADAY,
        timeframe="15m", min_bars=30,
        required_columns=("close", "nr7_prev", "prev_high", "prev_low",
                          "atr", "volume_ratio"),
        supported_regimes=("range", "bull"),
        hypothesis=("Range contraction to a 7-day minimum precedes range "
                    "expansion; breaking the narrow day's high the NEXT "
                    "session captures the expansion day itself, flat by the "
                    "close."),
        expected_behaviour=("NR7 days are ~1 in 7 by construction and the "
                            "break must come the very next session - roughly "
                            "one signal per stock per fortnight."),
        known_failure_modes=(
            "expansion resolves DOWN after a marginal upside poke (Crabel's "
            "own documented trap)",
            "gap opens far above the narrow day's high - the level is "
            "crossed at the open with no clean break bar",
            "contraction from illiquidity rather than genuine coiling",
        ),
        enabled=True,
    )

    #: NR7 Intraday's OWN execution (Crabel's day-trade form): stop below the
    #: narrow day's low (risking the compression range), NO target - the
    #: premise is the expansion day, ridden to the session square-off.
    execution = structural_intraday(stop_col="prev_low", target_kind="none")

    def __init__(self, settings=None) -> None:
        super().__init__(settings or Nr7IntradayParams())

    def min_history(self) -> int:
        # needs nr_lookback completed sessions of 15m bars behind the signal
        return max(self.settings.volume_window + 5, self.meta.min_bars)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        day = df["date"].dt.normalize()
        _, prev_high, prev_low, _ = prior_session_ohlc(df)
        df["prev_high"] = prev_high
        df["prev_low"] = prev_low
        # daily ranges from completed sessions; yesterday NR7 = yesterday's
        # range is the min of the 7 sessions ending with it (prior data only)
        session_range = (df.groupby(day)["high"].max()
                         - df.groupby(day)["low"].min())
        rolling_min = session_range.rolling(
            p.nr_lookback, min_periods=p.nr_lookback).min()
        yesterday_nr7 = (session_range <= rolling_min).shift(1)
        df["nr7_prev"] = day.map(yesterday_nr7).fillna(False).astype(float)
        df["atr"] = atr(df, p.atr_period)
        df["volume_ratio"] = volume_ratio(df["volume"], p.volume_window)
        return df

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        df = dataframe
        compressed = df["nr7_prev"] > 0.5
        breakout = crossed_above(df["close"], df["prev_high"])
        raw = (compressed & breakout).fillna(False)
        # Crabel's day-trade is THE breakout of the narrow day's high - one
        # trade per expansion day. Without this cap the level's re-crosses
        # re-fired in 36% of signal sessions (verification-phase finding).
        day = df["date"].dt.normalize()
        return (raw & (raw.groupby(day).cumsum() == 1)).fillna(False)

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        row = dataframe.iloc[-1]
        # compression depth: the narrow day's range vs ATR (smaller = better)
        rng = (float(row["prev_high"]) - float(row["prev_low"])) \
            if pd.notna(row["prev_high"]) and pd.notna(row["prev_low"]) else 0.0
        contraction = clip01(1.0 - (rng / float(row["atr"]) if row["atr"]
                                    else 1.0) / 25.0)
        volume = clip01(float(row["volume_ratio"]) / 2.0)
        return weighted(
            [Component("contraction", contraction, 0.5,
                       f"narrow-day range {rng:.2f}"),
             Component("volume", volume, 0.5,
                       f"volume_ratio {float(row['volume_ratio']):.2f}")],
            reason="broke the NR7 day's high on the following session")
