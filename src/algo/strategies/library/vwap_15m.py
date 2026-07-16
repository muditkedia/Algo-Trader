"""VWAP Trend Continuation - buy the reclaim of session VWAP in a VWAP-led day.

Thesis: institutions benchmark executions to VWAP. On a session where price
has spent most of its time above VWAP (buyers in control), a dip through VWAP
that is immediately reclaimed is absorption, not distribution - the mean
reversion to the institutional benchmark resolves in the trend's favour.
Entry is the bar that closes back above VWAP after trading below it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import atr, crossed_above, session_vwap, volume_ratio
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import (
    Component, ConfidenceScore, clip01, weighted,
)


@dataclass(frozen=True)
class VwapParams:
    #: share of PRIOR session bars that must have closed above VWAP
    above_share_min: float = 0.6
    #: bars into the session before the share statistic is meaningful
    warmup_bars: int = 4
    atr_period: int = 14
    volume_window: int = 20

    @classmethod
    def from_dict(cls, data) -> "VwapParams":
        return from_dict(cls, data)


class VwapTrendContinuation(StrategyProfile):

    meta = StrategyMeta(
        name="vwap_15m", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.INTRADAY,
        timeframe="15m", min_bars=30,
        required_columns=("close", "vwap", "above_share", "session_bar",
                          "atr", "volume_ratio"),
        supported_regimes=("bull",),
        hypothesis=("On buyer-controlled sessions (price mostly above VWAP), a "
                    "dip through VWAP that is reclaimed marks absorption at the "
                    "institutional benchmark and continuation follows."),
        expected_behaviour=("Zero to two signals per stock per session; only "
                            "fires after a genuine VWAP round-trip."),
        known_failure_modes=(
            "VWAP loss late in the session - reclaims near the close have no "
            "time to work before square-off",
            "trend-day reversals: the first VWAP loss of a distribution day "
            "looks identical at the moment of the reclaim",
            "low-volume drifts where VWAP is flat and crosses are noise",
        ),
        enabled=True,
    )

    def __init__(self, settings=None) -> None:
        super().__init__(settings or VwapParams())

    def min_history(self) -> int:
        return max(self.settings.volume_window + 5, self.meta.min_bars)

    # ------------------------------------------------------------ indicators

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        df["vwap"] = session_vwap(df)
        day = df["date"].dt.normalize()
        above = (df["close"] > df["vwap"]).astype(float)
        # expanding share of PRIOR bars above VWAP, per session
        df["above_share"] = above.groupby(day).transform(
            lambda s: s.shift(1).expanding().mean())
        df["session_bar"] = df.groupby(day).cumcount()
        df["atr"] = atr(df, p.atr_period)
        df["volume_ratio"] = volume_ratio(df["volume"], p.volume_window)
        return df

    # ---------------------------------------------------------------- signal

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        p = self.settings
        df = dataframe
        controlled = df["above_share"] >= p.above_share_min
        mature = df["session_bar"] >= p.warmup_bars
        reclaim = crossed_above(df["close"], df["vwap"])
        return (controlled & mature & reclaim).fillna(False)

    # ------------------------------------------------------------ confidence

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        row = dataframe.iloc[-1]
        # session control: how dominant buyers have been vs the minimum
        share = float(row["above_share"]) if pd.notna(row["above_share"]) else 0.0
        control = clip01((share - self.settings.above_share_min)
                         / (1.0 - self.settings.above_share_min)
                         if self.settings.above_share_min < 1.0 else 0.0)
        # reclaim strength: distance re-established above VWAP, in ATR
        strength = clip01(((float(row["close"]) - float(row["vwap"]))
                           / float(row["atr"])) / 0.5 if row["atr"] else 0.0)
        # participation on the reclaim bar
        volume = clip01(float(row["volume_ratio"]) / 2.0)
        return weighted(
            [Component("session_control", control, 0.40,
                       f"{share:.0%} of prior bars above VWAP"),
             Component("reclaim_strength", strength, 0.30,
                       f"close {((row['close'] - row['vwap']) / row['atr']):.2f} "
                       f"ATR above VWAP" if row["atr"] else "n/a"),
             Component("volume", volume, 0.30,
                       f"volume_ratio {float(row['volume_ratio']):.2f}")],
            reason="VWAP reclaimed on a buyer-controlled session",
        )
