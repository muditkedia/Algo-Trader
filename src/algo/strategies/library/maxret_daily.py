"""Anti-lottery (low MAX effect) - long the bottom decile of recent max return.

Thesis (Bali-Cakici-Whitelaw 2011): stocks with extreme recent single-day
returns are over-bought by lottery-seeking investors and subsequently
underperform; holding the LOW-MAX decile avoids that drag. A behavioural
cross-sectional anomaly on the DISTRIBUTION of recent returns, unrelated to
level, trend or total volatility. Pre-registered in
research/PREREGISTRATION_BATCH2.md (#7).
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
class MaxRetParams:
    window: int = 21
    quantile: float = 0.10

    @classmethod
    def from_dict(cls, data) -> "MaxRetParams":
        return from_dict(cls, data)


class AntiLotteryMax(CrossSectionalDecileStrategy):
    metric_col = "max_ret_21"
    top = False                         # LOWEST max-daily-return decile

    meta = StrategyMeta(
        name="maxret_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=30,
        required_columns=("close", "max_ret_21", "xs_flag"),
        supported_regimes=("bull", "range"),
        hypothesis=("Stocks with extreme recent single-day returns are bid up "
                    "by lottery-seekers and underperform; hold the low-MAX "
                    "decile. Behavioural, on the tail of the return "
                    "distribution - distinct from level/trend/vol sorts."),
        expected_behaviour="Monthly-ish turnover into calm, non-lottery names.",
        known_failure_modes=(
            "US evidence; the effect may differ in Indian retail-driven flow",
            "correlated with low-volatility (overlapping risk premium)",
            "long-only keeps only the low-MAX leg",
        ),
        enabled=True,
        horizon_bars=(21, 42, 63), max_hold_bars=63,
    )

    #: Swing execution owned by this strategy: ATR/structure stop,
    #: chandelier trail + profit locks, overnight allowed, horizon end
    #: at its declared max hold (no session square-off).
    execution = atr_trail_swing(meta.max_hold_bars)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or MaxRetParams())
        self.quantile = self.settings.quantile

    def min_history(self) -> int:
        return self.settings.window + 10

    def compute_metric(self, df: pd.DataFrame) -> pd.Series:
        return df["close"].pct_change().rolling(
            self.settings.window, min_periods=self.settings.window).max()
