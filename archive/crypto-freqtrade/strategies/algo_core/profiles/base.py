"""StrategyProfile - the interface every trading style implements.

A profile encapsulates one trading style's vectorized entry/exit signal
logic. The Freqtrade strategy class stays a thin orchestrator: it computes
shared indicators, asks the active profile for signals, and delegates
per-trade decisions to the decision engine and trade manager.

Profiles marked ``enabled = False`` are scaffolds: their interface and
intended design are fixed, but their signal logic is intentionally not
implemented yet. The registry refuses to activate them.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Tuple

import pandas as pd

logger = logging.getLogger(__name__)


class StrategyProfile(ABC):
    """One trading style. Subclasses define vectorized entry/exit signals."""

    #: Unique registry key; also used as the Freqtrade enter_tag.
    name: str = "base"
    #: Only enabled profiles can be activated by the strategy.
    enabled: bool = False
    #: Columns the profile needs; signals degrade to no-op when absent.
    required_columns: tuple = ()
    #: MarketRegime values (strings) this profile is allowed to trade. The
    #: strategy routes on this so new regimes need no interface change.
    supported_regimes: tuple = ("trend",)

    def __init__(self, settings=None, trade_manager=None) -> None:
        """All profiles share this signature so the registry can build any
        profile uniformly. ``settings`` is an ``AlgoSettings`` instance."""
        self.settings = settings
        self.trade_manager = trade_manager

    @abstractmethod
    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        """Boolean Series - True on candles where an entry candidate exists.

        This is the coarse, vectorized gate. The full GO / NO-GO evaluation
        (scoring, spread, sizing, rejection recording) happens per-trade in
        the decision engine via confirm_trade_entry.
        """

    @abstractmethod
    def exit_signal(self, dataframe: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        """(exit flags, exit reason tags) - objective exits only, never time-based."""

    def _missing_columns(self, dataframe: pd.DataFrame) -> list:
        return [c for c in self.required_columns if c not in dataframe.columns]

    def _no_signal(self, dataframe: pd.DataFrame, missing: list) -> pd.Series:
        logger.warning(
            "%s: no signals - missing columns %s", self.name, missing
        )
        return pd.Series(False, index=dataframe.index)
