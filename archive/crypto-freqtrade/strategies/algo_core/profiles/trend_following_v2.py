"""Trend Following v2 - active profile for AdaptiveTrend v2 (Phases D/E).

Reuses v1's mandatory multi-timeframe trend gate (4h regime, 1h trend, 15m
trend + structure, 5m execution trigger) and layers on a vectorized anti-chase
filter: reject entries extended above the 5m fast EMA or following a sharp 1h
run-up (Phase E: chasing extended moves is harmful). The per-trade
DecisionEngineV2 re-validates anti-chase and adds the cost gate.
"""

from __future__ import annotations

import pandas as pd

from algo_core.profiles.trend_following import TrendFollowingProfile


class TrendFollowingV2Profile(TrendFollowingProfile):

    name = "trend_following_v2"
    enabled = True
    supported_regimes = ("trend",)

    def __init__(self, settings=None, trade_manager=None, v2=None) -> None:
        super().__init__(settings, trade_manager)
        self.v2 = v2

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        base = super().entry_signal(dataframe)
        if self.v2 is None or "mom_1h" not in dataframe.columns \
                or "dist_fast_5m" not in dataframe.columns:
            return base
        anti_chase = (
            (dataframe["dist_fast_5m"] <= self.v2.max_ext_above_fast_5m)
            & (dataframe["mom_1h"] <= self.v2.max_mom_1h)
        )
        return (base & anti_chase).fillna(False)
