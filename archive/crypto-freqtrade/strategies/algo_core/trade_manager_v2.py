"""algo_core.trade_manager_v2 - v2 objective exits (Phase D).

Phase D showed v1's objective exits (1h EMA-cross family) fired a median of
~225 minutes AFTER the trade peak, closing 100% of those trades at a loss.
v2 replaces them with the same trend-reversal concept on the EXECUTION
timeframe: the 5m fast EMA crossing below the 5m slow EMA. This reacts ~12x
faster while still preserving trend participation (the position is held while
the 5m trend is intact).

Everything else is inherited from v1's TradeManager - the monotonic stop
ratchet, profit-locking, ATR trail, and hard-stop handling - but constructed
with v2's tighter RiskParams, so the proven trailing MECHANISM is preserved and
only its aggressiveness changes.
"""

from __future__ import annotations

import logging
from typing import Tuple

import numpy as np
import pandas as pd

from algo_core.indicators import crossed_below
from algo_core.trade_manager import TradeManager

logger = logging.getLogger(__name__)


class TradeManagerV2(TradeManager):

    EXIT_COLUMNS = ("ema_fast", "ema_slow")

    def exit_conditions(self, dataframe: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        """5m trend-reversal exit: fast EMA crosses below slow EMA (execution TF)."""
        missing = [c for c in self.EXIT_COLUMNS if c not in dataframe.columns]
        if missing:
            logger.warning("v2 exit skipped, missing columns: %s", missing)
            no_exit = pd.Series(False, index=dataframe.index)
            return no_exit, pd.Series("", index=dataframe.index)

        reversal = crossed_below(dataframe["ema_fast"], dataframe["ema_slow"]).fillna(False)
        tags = pd.Series(
            np.where(reversal, "v2_exit_5m_trend_reversal", ""),
            index=dataframe.index)
        return reversal, tags
