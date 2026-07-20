"""Cross-sectional short-term reversal - long the prior-week loser decile.

Thesis (Jegadeesh 1990; Lehmann 1990): last week's cross-sectional losers
rebound over 1-4 weeks as liquidity-driven price pressure unwinds. Long-only:
hold the bottom decile of 5-day return. This is the OPPOSITE sign to momentum
and the first mean-reversion hypothesis the platform has tested.

Pre-registered weakness (Avramov-Chordia-Goyal): reversal profits concentrate in
illiquid names and are largely eaten by cost - the frozen gate should catch it.
Pre-registered in research/PREREGISTRATION_BATCH2.md (#6).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.execution import atr_trail_swing
from algo.strategies.base import StrategyMeta
from algo.strategies.cross_section import CrossSectionalDecileStrategy


@dataclass(frozen=True)
class XsRevParams:
    lookback: int = 5
    quantile: float = 0.10

    @classmethod
    def from_dict(cls, data) -> "XsRevParams":
        return from_dict(cls, data)


class CrossSectionalReversal(CrossSectionalDecileStrategy):
    metric_col = "ret_5d"
    top = False                         # biggest LOSERS

    meta = StrategyMeta(
        name="xsrev_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=20,
        required_columns=("close", "ret_5d", "xs_flag"),
        supported_regimes=("range", "bull", "bear"),
        hypothesis=("Prior-week cross-sectional losers rebound over 1-4 weeks "
                    "as liquidity pressure unwinds. Opposite sign to momentum; "
                    "the first mean-reversion test on the platform."),
        expected_behaviour="High turnover (weekly); cost-sensitive by design.",
        known_failure_modes=(
            "profits concentrate in illiquid names and are eaten by cost",
            "the flat slippage model is optimistic exactly here",
            "catches falling knives in downtrends",
        ),
        enabled=True,
        horizon_bars=(5, 10, 21), max_hold_bars=21,
    )

    #: Swing execution owned by this strategy: ATR/structure stop,
    #: chandelier trail + profit locks, overnight allowed, horizon end
    #: at its declared max hold (no session square-off).
    execution = atr_trail_swing(meta.max_hold_bars)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or XsRevParams())
        self.quantile = self.settings.quantile

    def min_history(self) -> int:
        return self.settings.lookback + 10

    def compute_metric(self, df: pd.DataFrame) -> pd.Series:
        return df["close"] / df["close"].shift(self.settings.lookback) - 1.0
