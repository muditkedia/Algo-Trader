"""RegimeDetector - classifies the prevailing market regime.

The detector is a separate module so future regimes (RANGE, VOLATILE, ...)
can be added without touching the Freqtrade strategy interface. The strategy
always calls ``detect(row)`` and routes on the returned ``MarketRegime`` (a
profile only trades regimes listed in its ``supported_regimes``).

Current behaviour: every market is classified as TREND - the only regime the
active trend_following profile trades. The ``_classify`` extension point below
shows where RANGE / HIGH_VOLATILITY / LOW_VOLATILITY logic will go; it will be
implemented and validated before any regime-specific profile is activated.
Until then the detector deliberately does not guess.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

import pandas as pd

from algo_core.settings import EngineParams

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    TREND = "trend"
    RANGE = "range"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    UNKNOWN = "unknown"


class RegimeDetector:
    """Classifies one analyzed candle row into a MarketRegime."""

    def __init__(self, engine: EngineParams) -> None:
        self.engine = engine

    def detect(self, row: Optional[pd.Series]) -> MarketRegime:
        """Return the market regime for the given analyzed candle row.

        For now this always returns TREND. The classification hook is kept
        separate so expansion needs no change to the strategy or its callers.
        """
        return self._classify(row)

    def _classify(self, row: Optional[pd.Series]) -> MarketRegime:
        # Extension point. Intended future logic (not yet enabled):
        #   * TREND           - 1h/4h ADX above adx_min and EMA alignment
        #   * RANGE           - ADX below floor, price oscillating around slow EMA
        #   * HIGH_VOLATILITY - ATR% above the tradeable band
        #   * LOW_VOLATILITY  - ATR% below the tradeable band
        #   * UNKNOWN         - insufficient data to classify
        # Implementing these requires evidence (backtest) before activation,
        # so the detector currently commits only to TREND.
        return MarketRegime.TREND
