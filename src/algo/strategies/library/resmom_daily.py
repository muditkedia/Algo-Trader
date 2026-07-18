"""Residual (market-adjusted) momentum - long the top decile of alpha momentum.

Thesis (Blitz-Huij-Martens 2011): momentum measured on returns RESIDUAL to the
market isolates stock-specific under-reaction and strips the factor bets that
cause momentum crashes - historically better risk-adjusted than raw momentum.
Neither batch 1 nor plain cross-sectional momentum removes market beta from the
ranking signal; this does. Pre-registered in
research/PREREGISTRATION_BATCH2.md (#2).

Residual momentum here: with the equal-weight universe as the market, estimate a
rolling beta per stock, form the residual daily return (stock - beta*market),
and rank the cumulative residual return over [t-window, t-skip]. The market
aggregate makes this a cross-sectional computation, so it lives entirely in
``prepare_cross_section``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.strategies.base import StrategyMeta
from algo.strategies.cross_section import (
    CrossSectionalDecileStrategy, decile_flag, market_return, scatter_series,
)


@dataclass(frozen=True)
class ResMomParams:
    window: int = 126
    skip: int = 21
    beta_window: int = 126
    quantile: float = 0.10

    @classmethod
    def from_dict(cls, data) -> "ResMomParams":
        return from_dict(cls, data)


class ResidualMomentum(CrossSectionalDecileStrategy):
    metric_col = "resmom"
    top = True

    meta = StrategyMeta(
        name="resmom_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=170,
        required_columns=("close", "resmom", "xs_flag"),
        supported_regimes=("bull", "range"),
        hypothesis=("Momentum on market-residual returns isolates stock-specific "
                    "under-reaction and avoids momentum-crash factor bets; hold "
                    "the top residual-momentum decile."),
        expected_behaviour="As momentum but with smaller crash exposure.",
        known_failure_modes=(
            "needs a market model - more researcher choices than raw momentum",
            "equal-weight market is a crude factor proxy",
            "long-only truncates the short leg",
        ),
        enabled=True,
        horizon_bars=(21, 42, 63), max_hold_bars=63,
    )

    def __init__(self, settings=None) -> None:
        super().__init__(settings or ResMomParams())
        self.quantile = self.settings.quantile

    def min_history(self) -> int:
        p = self.settings
        return max(p.window, p.beta_window) + p.skip + 10

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["stock_ret"] = out["close"].pct_change()
        return out

    def prepare_cross_section(self, frames):
        p = self.settings
        mkt = market_return(frames)                    # EW universe daily return
        scatter_series(frames, mkt, "mkt_ret")
        for df in frames.values():
            s = df["stock_ret"]
            m = df["mkt_ret"]
            # rolling beta = cov(s, m) / var(m) over beta_window (causal)
            cov = s.rolling(p.beta_window, min_periods=p.beta_window).cov(m)
            var = m.rolling(p.beta_window, min_periods=p.beta_window).var()
            beta = cov / var.replace(0.0, np.nan)
            resid = s - beta * m
            # cumulative residual return over [t-window, t-skip]
            df["resmom"] = resid.rolling(
                p.window - p.skip, min_periods=p.window - p.skip).sum().shift(p.skip)
        decile_flag(frames, "resmom", "xs_flag", quantile=p.quantile, top=True)
        return frames
