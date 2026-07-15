"""Trade manager - in-trade stop management and objective exit conditions.

Responsibilities after entry:

* Set the initial stop from the risk engine (ATR/structure based, capped by
  the hard emergency stop) and persist it on the trade.
* Ratchet the stop tighter as profit-lock tiers unlock and the ATR trail
  advances. A stop is NEVER widened: the manager keeps the running maximum
  stop price in the trade's custom data, and Freqtrade itself also refuses
  stop reductions.
* Define the objective exit conditions (trend reversal, trend exhaustion,
  momentum breakdown). There are deliberately NO time-based exits: a trade
  is held while trend-strength criteria remain valid.
"""

from __future__ import annotations

import logging
import math
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from algo_core.indicators import crossed_below
from algo_core.risk_engine import initial_stop_pct, trailing_stop_price
from algo_core.settings import RiskParams

logger = logging.getLogger(__name__)

#: Columns required to evaluate the exit conditions.
EXIT_COLUMNS = (
    "ema_fast_1h",
    "ema_slow_1h",
    "adx_15m",
    "rsi_15m",
    "ema_fast_15m",
    "ema_slow_15m",
    "close",
)


def _value(row: pd.Series, key: str) -> Optional[float]:
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


class TradeManager:
    """Manages stops and exits for open trades using RiskParameters."""

    #: Keys used to persist state on the trade via Freqtrade custom data.
    KEY_INITIAL_STOP_PCT = "initial_stop_pct"
    KEY_STOP_PRICE = "stop_price"

    def __init__(self, params: RiskParams) -> None:
        self.params = params

    # ---------------------------------------------------------------- stops

    def _initial_stop_price(self, trade, row: pd.Series) -> float:
        """Initial stop price for the trade, computed once and persisted."""
        stop_pct = trade.get_custom_data(self.KEY_INITIAL_STOP_PCT)
        if stop_pct is None:
            stop_pct = initial_stop_pct(
                _value(row, "close"),
                _value(row, "atr_15m"),
                _value(row, "swing_low_15m"),
                self.params,
            )
            # The decision engine rejects entries needing a stop wider than the
            # hard stop; this fallback only guards data gaps mid-trade.
            stop_pct = min(stop_pct or self.params.hard_stop_pct,
                           self.params.hard_stop_pct)
            trade.set_custom_data(self.KEY_INITIAL_STOP_PCT, stop_pct)
        return trade.open_rate * (1 - stop_pct)

    def stoploss_ratio(
        self,
        trade,
        current_rate: float,
        current_profit: float,
        row: pd.Series,
    ) -> Optional[float]:
        """Stop-loss ratio (relative to current_rate) for custom_stoploss.

        Monotonic by construction: the returned stop price is the running
        maximum of the initial stop, unlocked profit-lock tiers, and the ATR
        trail. Returns None when no stop can be computed yet.
        """
        if current_rate <= 0:
            return None
        base = self._initial_stop_price(trade, row)
        previous = trade.get_custom_data(self.KEY_STOP_PRICE)
        previous = base if previous is None else max(base, previous)

        candidate = trailing_stop_price(
            trade.open_rate,
            current_rate,
            current_profit,
            _value(row, "atr_15m"),
            self.params,
        )
        stop_price = max(previous, candidate) if candidate else previous
        # A stop can never sit at/above the current price; cap just below it.
        stop_price = min(stop_price, current_rate * (1 - 1e-4))
        if stop_price > previous:
            trade.set_custom_data(self.KEY_STOP_PRICE, stop_price)

        return stop_price / current_rate - 1

    # ---------------------------------------------------------------- exits

    def exit_conditions(self, dataframe: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        """Vectorized objective exit signals: (exit flags, exit reason tags).

        Conditions (long side), in priority order:
          exit_1h_trend_reversal   1h fast EMA crosses below the slow EMA.
          exit_trend_exhaustion    15m ADX collapses while price loses the 15m slow EMA.
          exit_momentum_breakdown  15m RSI breaks down while price loses the 15m fast EMA.
        """
        missing = [c for c in EXIT_COLUMNS if c not in dataframe.columns]
        if missing:
            logger.warning("Exit conditions skipped, missing columns: %s", missing)
            no_exit = pd.Series(False, index=dataframe.index)
            return no_exit, pd.Series("", index=dataframe.index)

        reversal = crossed_below(dataframe["ema_fast_1h"], dataframe["ema_slow_1h"])
        exhaustion = (dataframe["adx_15m"] < self.params.exit_adx_floor) & (
            dataframe["close"] < dataframe["ema_slow_15m"]
        )
        breakdown = (dataframe["rsi_15m"] < self.params.exit_rsi_floor) & (
            dataframe["close"] < dataframe["ema_fast_15m"]
        )

        flags = (reversal | exhaustion | breakdown).fillna(False)
        tags = pd.Series(
            np.select(
                [reversal.fillna(False), exhaustion.fillna(False), breakdown.fillna(False)],
                ["exit_1h_trend_reversal", "exit_trend_exhaustion", "exit_momentum_breakdown"],
                default="",
            ),
            index=dataframe.index,
        )
        return flags, tags
