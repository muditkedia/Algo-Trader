"""1-hour Volatility Expansion Breakout - buy the break out of a squeeze.

Thesis: when 1h Bollinger bandwidth compresses (volatility contraction), the
eventual range break tends to travel - energy stored during compression is
released directionally. Entry is the bar that closes across the upper band
after a multi-bar squeeze; the squeeze is measured on PRIOR bars only, so the
breakout bar's own expansion cannot disqualify it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import (
    atr, bollinger, bollinger_bandwidth, crossed_above, volume_ratio,
)
from algo.execution import atr_trail_intraday
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import (
    Component, ConfidenceScore, clip01, weighted,
)


@dataclass(frozen=True)
class VolExpParams:
    bb_period: int = 20
    bb_std: float = 2.0
    #: prior-bar bandwidth must stay at/below this for the whole squeeze window
    bandwidth_max: float = 0.03
    squeeze_bars: int = 3
    atr_period: int = 14
    volume_window: int = 20

    @classmethod
    def from_dict(cls, data) -> "VolExpParams":
        return from_dict(cls, data)


class VolatilityExpansionBreakout1h(StrategyProfile):

    meta = StrategyMeta(
        name="volexp_1h", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.INTRADAY,
        timeframe="1h", min_bars=40,
        required_columns=("close", "high", "low", "bb_upper", "bb_width",
                          "atr", "volume_ratio"),
        supported_regimes=("range", "bull"),
        hypothesis=("Volatility is cyclical: multi-bar Bollinger compression on "
                    "the 1h chart precedes directional expansion, and the first "
                    "close above the upper band captures the release."),
        expected_behaviour=("Rare, clustered signals - only after genuine "
                            "compression; inactive in already-volatile tape."),
        known_failure_modes=(
            "false break: expansion fails and price re-enters the band",
            "news gaps that 'break out' without any stored-energy dynamics",
            "thin names where the squeeze reflects illiquidity, not coiling",
        ),
        enabled=True,
    )

    #: This strategy's OWN execution: no published exit is recorded for it
    #: anywhere in this repository, so it declares its pre-reset behaviour as
    #: its own - ATR/structure stop, chandelier trail + profit locks, session
    #: square-off, never overnight.
    execution = atr_trail_intraday()

    def __init__(self, settings=None) -> None:
        super().__init__(settings or VolExpParams())

    def min_history(self) -> int:
        p = self.settings
        return max(p.bb_period + p.squeeze_bars + 5, p.volume_window + 5,
                   self.meta.min_bars)

    # ------------------------------------------------------------ indicators

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        upper, mid, lower = bollinger(df["close"], p.bb_period, p.bb_std)
        df["bb_upper"] = upper
        df["bb_width"] = bollinger_bandwidth(df["close"], p.bb_period, p.bb_std)
        df["atr"] = atr(df, p.atr_period)
        df["volume_ratio"] = volume_ratio(df["volume"], p.volume_window)
        return df

    # ---------------------------------------------------------------- signal

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        p = self.settings
        df = dataframe
        # squeeze on PRIOR bars only (the breakout bar itself may expand)
        prior_width_max = (df["bb_width"].shift(1)
                           .rolling(p.squeeze_bars, min_periods=p.squeeze_bars)
                           .max())
        squeezed = prior_width_max <= p.bandwidth_max
        breakout = crossed_above(df["close"], df["bb_upper"])
        return (squeezed & breakout).fillna(False)

    # ------------------------------------------------------------ confidence

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or len(dataframe) < 2:
            return ConfidenceScore.zero("missing columns")
        p = self.settings
        row, prev = dataframe.iloc[-1], dataframe.iloc[-2]
        # squeeze tightness: how far below the ceiling the prior bandwidth sat
        tightness = clip01((p.bandwidth_max - float(prev["bb_width"]))
                           / p.bandwidth_max) if p.bandwidth_max else 0.0
        # participation: breakout volume (2x average => 1.0)
        volume = clip01(row["volume_ratio"] / 2.0)
        # bar quality: close location within the breakout bar's range
        bar_range = float(row["high"] - row["low"])
        clv = clip01((float(row["close"] - row["low"]) / bar_range)
                     if bar_range > 0 else 0.0)
        return weighted(
            [Component("squeeze_tightness", tightness, 0.40,
                       f"prior bandwidth {float(prev['bb_width']):.4f} "
                       f"(max {p.bandwidth_max})"),
             Component("volume", volume, 0.30,
                       f"volume_ratio {row['volume_ratio']:.2f}"),
             Component("close_strength", clv, 0.30,
                       f"close at {clv:.0%} of bar range")],
            reason="1h squeeze released through the upper band",
        )
