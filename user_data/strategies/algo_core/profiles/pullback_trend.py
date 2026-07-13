"""Pullback Trend - INACTIVE scaffold (explicitly requested placeholder).

Intended design (to be implemented before activation):
    Enter established uptrends on deeper retracements than the trend
    following profile tolerates - price pulling back to the 15m slow EMA /
    a fib zone while the 4h and 1h regimes stay intact - and trigger on the
    5m reclaim of the fast EMA. Tighter initial stops (below the pullback
    low), same GO / NO-GO and risk pipeline.

Activation checklist: implement signals, add profile-specific engine
thresholds if needed, enable, and take it through the full validation
vertical slice (backtest, bias detection, documentation).
"""

from __future__ import annotations

from typing import Tuple

import pandas as pd

from algo_core.profiles.base import StrategyProfile


class PullbackTrendProfile(StrategyProfile):

    name = "pullback_trend"
    enabled = False

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        raise NotImplementedError(
            "pullback_trend is a scaffold - implement and enable before use."
        )

    def exit_signal(self, dataframe: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        raise NotImplementedError(
            "pullback_trend is a scaffold - implement and enable before use."
        )
