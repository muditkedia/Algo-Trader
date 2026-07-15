"""Mean Reversion - INACTIVE scaffold (explicitly requested placeholder).

Intended design (to be implemented before activation):
    Fade short-term overextensions inside ranging regimes: 4h/1h ADX low
    (no trend), price stretched beyond a volatility band (e.g. Bollinger /
    ATR envelope) with RSI extremes, entering on 5m reversal confirmation.
    Requires its own exit logic (revert-to-mean targets) and stricter
    volatility caps than the trend profiles.

Activation checklist: implement signals and range-regime detection, enable,
and take it through the full validation vertical slice.
"""

from __future__ import annotations

from typing import Tuple

import pandas as pd

from algo_core.profiles.base import StrategyProfile


class MeanReversionProfile(StrategyProfile):

    name = "mean_reversion"
    enabled = False

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        raise NotImplementedError(
            "mean_reversion is a scaffold - implement and enable before use."
        )

    def exit_signal(self, dataframe: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        raise NotImplementedError(
            "mean_reversion is a scaffold - implement and enable before use."
        )
