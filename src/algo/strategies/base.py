"""StrategyProfile - the plugin interface every strategy implements.

Per the approved research-first design, a strategy declares only:

  * its entry signal (a vectorized boolean condition), and
  * metadata: required indicator columns, direction, holding scope, the regime
    hypothesis, expected behaviour, and known failure modes.

Everything else - measurement, confidence, ranking, risk, exits, execution,
reporting - is common platform machinery. There is deliberately NO exit signal
here: exits are handled uniformly by the trade manager (the crypto phase proved
structural per-strategy exits were value-destructive, docs/DECISIONS.md D-006).

A strategy holds no shared mutable state, so adding one cannot affect another.
This module is the generalization of the crypto ``profiles/base.py`` interface;
its concrete crypto profiles are archived, not reused.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field

import pandas as pd

from algo.core.enums import Direction, HoldingScope
from algo.core.logging import get_logger
from algo.strategies.confidence import ConfidenceScore

logger = get_logger("strategies")


@dataclass(frozen=True)
class StrategyMeta:
    """Declarative description of a strategy. One instance per strategy class."""

    name: str
    version: str
    direction: Direction
    holding_scope: HoldingScope
    #: Indicator columns the entry_signal needs; the missing-column guard uses
    #: this, and the research engine records it as the strategy's contract.
    required_columns: tuple = ()
    #: Regime labels (e.g. ("bull",)) the strategy hypothesizes it works in;
    #: recorded for evidence, not a hard gate until evidence supports it.
    supported_regimes: tuple = ()
    #: The edge thesis - why this should have an edge (measured, never assumed).
    hypothesis: str = ""
    expected_behaviour: str = ""
    known_failure_modes: tuple = ()
    #: Only enabled strategies can be instantiated for scanning/trading.
    enabled: bool = False
    #: Bar timeframe the strategy trades on (the scanner loads bars per this).
    timeframe: str = "1d"
    #: Default minimum history (bars) for warmed-up indicators; strategies with
    #: parameter-dependent warmups override min_history() instead.
    min_bars: int = 2
    #: Trailing-stop mode this strategy's thesis prefers: atr (default,
    #: = the promoted engine's behaviour) | percentage | structure |
    #: volatility. Purely declarative - the execution layer honours it, so
    #: existing strategies need no changes (see algo.risk.trailing).
    trail_mode: str = "atr"


class StrategyProfile(ABC):
    """One trading style. Subclasses set ``meta`` and implement ``entry_signal``."""

    #: MUST be overridden by every concrete subclass.
    meta: StrategyMeta = None  # type: ignore[assignment]

    def __init__(self, settings=None) -> None:
        """``settings`` is the strategy's own frozen-dataclass parameters
        (optional); the research engine can hand these to the sensitivity sweep.
        """
        self.settings = settings

    # ------------------------------------------------------------- accessors

    @property
    def name(self) -> str:
        return self.meta.name

    @property
    def enabled(self) -> bool:
        return bool(self.meta.enabled)

    def describe(self) -> dict:
        return asdict(self.meta)

    # --------------------------------------------------------------- signal

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        """Indicator preparation: return a NEW frame with the columns this
        strategy's ``entry_signal``/``confidence`` need. Must not mutate the
        input (the scanner shares the raw bars across strategies). Default:
        pass-through for strategies that need only raw OHLCV."""
        return dataframe

    @abstractmethod
    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        """Boolean Series: True on bars where an entry candidate exists.

        Evaluated on the PREPARED frame. This is the coarse vectorized gate;
        ranking, risk, and disposition happen downstream in common machinery.
        Conditions should be edge-triggered (fire on the transition bar, not on
        every bar a state holds) so a signal is emitted once per setup.
        """

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        """Heuristic signal-quality score for the LAST bar of the prepared
        frame, with named components (see strategies/confidence.py). These are
        recorded as evidence and later recalibrated by the research engine -
        they are hypotheses, not probabilities. Default: zero."""
        return ConfidenceScore.zero("no confidence model")

    def min_history(self) -> int:
        """Bars needed for warmed-up indicators. Defaults to ``meta.min_bars``;
        override when the warmup depends on configurable parameters."""
        return int(self.meta.min_bars)

    # ------------------------------------------------------------- helpers

    def missing_columns(self, dataframe: pd.DataFrame) -> list:
        return [c for c in self.meta.required_columns
                if c not in dataframe.columns]

    def no_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        """A blank (all-False) signal aligned to ``dataframe`` - never raises."""
        return pd.Series(False, index=dataframe.index)
