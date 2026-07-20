"""Betting against beta - long the bottom decile of market beta.

Thesis (Frazzini-Pedersen 2014): low-beta stocks deliver higher risk-adjusted
returns than high-beta; leverage-constrained investors bid up high beta. A
standard factor, distinct from low-volatility (which ranks TOTAL volatility, not
systematic beta). Long-only: hold the low-beta decile. Beta is estimated against
the equal-weight universe, so this is cross-sectional. Pre-registered in
research/PREREGISTRATION_BATCH2.md (#5).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.execution import atr_trail_swing
from algo.strategies.base import StrategyMeta
from algo.strategies.cross_section import (
    CrossSectionalDecileStrategy, decile_flag, market_return, scatter_series,
)


@dataclass(frozen=True)
class BabParams:
    beta_window: int = 252
    quantile: float = 0.10

    @classmethod
    def from_dict(cls, data) -> "BabParams":
        return from_dict(cls, data)


class BettingAgainstBeta(CrossSectionalDecileStrategy):
    metric_col = "beta"
    top = False                         # LOWEST beta decile

    meta = StrategyMeta(
        name="bab_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=270,
        required_columns=("close", "beta", "xs_flag"),
        supported_regimes=("bull", "range", "bear"),
        hypothesis=("Low-beta stocks earn higher risk-adjusted returns; hold "
                    "the low-beta decile (betting against beta). Ranks "
                    "SYSTEMATIC beta, distinct from low-vol's total volatility."),
        expected_behaviour="Low turnover; defensive; lags in strong bulls.",
        known_failure_modes=(
            "risk-adjusted claim vs the gate's raw-return bars",
            "beta estimated on an equal-weight proxy, not a true market cap index",
            "overlaps low-volatility",
        ),
        enabled=True,
        horizon_bars=(21, 63, 126), max_hold_bars=126,
    )

    #: Swing execution owned by this strategy: ATR/structure stop,
    #: chandelier trail + profit locks, overnight allowed, horizon end
    #: at its declared max hold (no session square-off).
    execution = atr_trail_swing(meta.max_hold_bars)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or BabParams())
        self.quantile = self.settings.quantile

    def min_history(self) -> int:
        return self.settings.beta_window + 15

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["stock_ret"] = out["close"].pct_change()
        return out

    def prepare_cross_section(self, frames):
        p = self.settings
        mkt = market_return(frames)
        scatter_series(frames, mkt, "mkt_ret")
        for df in frames.values():
            s, m = df["stock_ret"], df["mkt_ret"]
            cov = s.rolling(p.beta_window, min_periods=p.beta_window).cov(m)
            var = m.rolling(p.beta_window, min_periods=p.beta_window).var()
            df["beta"] = cov / var.replace(0.0, np.nan)
        decile_flag(frames, "beta", "xs_flag", quantile=p.quantile, top=False)
        return frames
