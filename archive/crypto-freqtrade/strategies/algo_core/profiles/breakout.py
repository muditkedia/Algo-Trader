"""Breakout - INACTIVE scaffold (explicitly requested placeholder).

Intended design (to be implemented before activation):
    Enter on 5m/15m closes above a defined consolidation range or prior
    swing high, confirmed by a volume surge and expanding ATR, with the 4h
    regime neutral-to-bullish. Initial stop below the breakout base; the
    volatility check inverts relative to trend following (expansion is the
    signal, not a filter).

Activation checklist: implement signals, tune the range-detection window,
enable, and take it through the full validation vertical slice.
"""

from __future__ import annotations

from typing import Tuple

import pandas as pd

from algo_core.profiles.base import StrategyProfile


class BreakoutProfile(StrategyProfile):

    name = "breakout"
    enabled = False

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        raise NotImplementedError(
            "breakout is a scaffold - implement and enable before use."
        )

    def exit_signal(self, dataframe: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        raise NotImplementedError(
            "breakout is a scaffold - implement and enable before use."
        )
