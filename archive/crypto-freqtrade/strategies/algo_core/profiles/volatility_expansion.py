"""Volatility Expansion - INACTIVE scaffold (explicitly requested placeholder).

Intended design (to be implemented before activation):
    Enter when volatility regime shifts from compression to expansion
    (e.g. ATR% breaking out of a squeeze, Bollinger bandwidth expansion)
    in the direction of the fresh impulse, on 5m confirmation. Wider ATR
    stop multiples and faster profit-lock tiers than trend following.

Activation checklist: implement signals and squeeze detection, revisit the
risk parameters for expansion conditions, enable, and take it through the
full validation vertical slice.
"""

from __future__ import annotations

from typing import Tuple

import pandas as pd

from algo_core.profiles.base import StrategyProfile


class VolatilityExpansionProfile(StrategyProfile):

    name = "volatility_expansion"
    enabled = False

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        raise NotImplementedError(
            "volatility_expansion is a scaffold - implement and enable before use."
        )

    def exit_signal(self, dataframe: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        raise NotImplementedError(
            "volatility_expansion is a scaffold - implement and enable before use."
        )
