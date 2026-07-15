"""algo_core.trade_manager_v3 - v3 objective exit (controlled exit refinement).

Single-variable change vs v2: replace the 5m EMA-cross structural exit (which
fired on routine pullbacks, 0.1% win, -897 USDT in Phase E) with a
CONFIRMATION-BASED 15m structural exit.

The exit requires TWO conditions simultaneously:
  1. 15m trend has turned down   : ema_fast_15m < ema_slow_15m  (~5h vs ~12.5h EMA)
  2. price confirms the break     : close < ema_slow_15m

Rationale:
  * The 15m timeframe is 3x coarser than 5m, so shallow intra-trend pullbacks
    that hold above the 15m structure do NOT trigger it (fixes v2's noise).
  * Requiring price below the 15m slow EMA is the confirmation - the exit only
    fires on a genuine 15m structural break, not a fleeting EMA touch.
  * A 15m death-cross forms ~4x faster in wall-clock than v1's 1h death-cross,
    so v3 still exits materially earlier than the original 1h exit.

Everything else (monotonic stop ratchet, tighter v2 trailing, hard stop) is
inherited unchanged - only exit_conditions differs.
"""

from __future__ import annotations

import logging
from typing import Tuple

import numpy as np
import pandas as pd

from algo_core.trade_manager import TradeManager

logger = logging.getLogger(__name__)


class TradeManagerV3(TradeManager):

    EXIT_COLUMNS = ("ema_fast_15m", "ema_slow_15m", "close")

    def exit_conditions(self, dataframe: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        """15m confirmed trend-reversal exit."""
        missing = [c for c in self.EXIT_COLUMNS if c not in dataframe.columns]
        if missing:
            logger.warning("v3 exit skipped, missing columns: %s", missing)
            no_exit = pd.Series(False, index=dataframe.index)
            return no_exit, pd.Series("", index=dataframe.index)

        trend_down = dataframe["ema_fast_15m"] < dataframe["ema_slow_15m"]
        price_confirms = dataframe["close"] < dataframe["ema_slow_15m"]
        flags = (trend_down & price_confirms).fillna(False)
        tags = pd.Series(
            np.where(flags, "v3_exit_15m_confirmed_reversal", ""),
            index=dataframe.index)
        return flags, tags
