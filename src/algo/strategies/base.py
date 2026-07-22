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
from dataclasses import asdict, dataclass

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
    #: Stable identifier in the governing strategy specification. Historical
    #: implementations without a specification retain the empty default.
    spec_id: str = ""
    #: Strategies in the same group may suppress later signals on a symbol for
    #: the rest of the session after one executes.
    exclusive_group: str = ""
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
    #: PRE-REGISTERED research horizon, in bars of ``timeframe``.
    #:
    #: ``horizon_bars`` are the forward-return horizons the edge lab measures;
    #: ``max_hold_bars`` caps the simulated managed trade. Together they encode
    #: the strategy's OWN claim about how long its edge needs to pay out, and
    #: they are frozen with ``version`` BEFORE measurement. This matters: the
    #: horizon is the one parameter that could be re-picked after seeing a bad
    #: verdict, which is precisely the tuning D-026 forbids. Declaring it here
    #: makes it part of the hypothesis and version-bumps any change to it.
    #:
    #: Defaults reproduce the Phase-5/7 measurement exactly, so every verdict
    #: already on record stays reproducible.
    horizon_bars: tuple = (1, 2, 4, 8)
    max_hold_bars: int = 8

    def __post_init__(self) -> None:
        # Fail at import, not mid-sweep: a mis-declared horizon would silently
        # measure a different hypothesis than the one written above it.
        horizons = tuple(self.horizon_bars)
        if not horizons or any(int(h) < 1 for h in horizons):
            raise ValueError(
                f"{self.name}: horizon_bars must be a non-empty tuple of "
                f"positive bar counts, got {self.horizon_bars!r}")
        if int(self.max_hold_bars) < max(horizons):
            raise ValueError(
                f"{self.name}: max_hold_bars ({self.max_hold_bars}) is shorter "
                f"than the longest measured horizon ({max(horizons)}) - the "
                "simulated trade could never reach the horizon its edge is "
                "claimed at")


class StrategyProfile(ABC):
    """One trading style. Subclasses set ``meta`` and implement ``entry_signal``."""

    #: MUST be overridden by every concrete subclass.
    meta: StrategyMeta = None  # type: ignore[assignment]

    #: The strategy's OWN execution declaration (an
    #: ``algo.execution.ExecutionSpec``): entry timing, stop-loss, profit
    #: target, trailing rules, session restrictions, holding limit and
    #: square-off. The execution engine interprets this declaration - there is
    #: no platform-imposed stop/trail/exit (project-reset design change,
    #: 2026-07-18; supersedes the D-006 uniform-exit model for the execution
    #: path). Every registered strategy MUST declare one (enforced by test).
    #: The frozen research simulator does not read it, so recorded research
    #: verdicts are unaffected.
    execution = None  # type: ignore[assignment]

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

    #: A cross-sectional strategy ranks each stock against its peers, so its
    #: signal cannot be computed from one symbol's frame alone. Overriding this
    #: declares that need: the engine calls it ONCE per sweep with every
    #: prepared per-symbol frame, and the override returns them augmented with
    #: cross-sectional columns (e.g. a top-decile flag) that ``entry_signal``
    #: then reads per symbol as usual. Purely additive: the default is a no-op,
    #: so every per-symbol strategy (all of batch 1) is completely unaffected,
    #: and the measurement / selection gate downstream are untouched.
    cross_sectional: bool = False

    def prepare_cross_section(
        self, frames: "dict[str, pd.DataFrame]") -> "dict[str, pd.DataFrame]":
        """Inject cross-sectional columns across all symbols' prepared frames.

        ``frames`` maps symbol -> the frame returned by ``prepare`` for that
        symbol (only symbols with enough history are present). Return the same
        mapping with any cross-sectional columns added in place. Default no-op.
        """
        return frames

    @abstractmethod
    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        """Boolean Series: True on bars where an entry candidate exists.

        Evaluated on the PREPARED frame. This is the coarse vectorized gate;
        ranking, risk, and disposition happen downstream in common machinery.
        Conditions should be edge-triggered (fire on the transition bar, not on
        every bar a state holds) so a signal is emitted once per setup.
        """

    def entry_signals(self, dataframe: pd.DataFrame) -> dict:
        """Direction -> signal series, backward-compatible for long-only code.

        Bidirectional strategies override this method. Existing strategies keep
        implementing ``entry_signal`` and therefore retain identical behavior.
        """
        if self.meta.direction == Direction.BOTH:
            raise NotImplementedError(
                f"{self.name} declares both directions but does not implement "
                "entry_signals()")
        return {self.meta.direction: self.entry_signal(dataframe)}

    def confidence_for(self, dataframe: pd.DataFrame,
                       direction: Direction) -> ConfidenceScore:
        """Directional confidence hook; defaults to the existing score."""
        return self.confidence(dataframe)

    def regime_score(self, dataframe: pd.DataFrame,
                     direction: Direction) -> float:
        """Directional regime score normalized to [0, 1]."""
        return 0.0

    def entry_trigger(self, dataframe: pd.DataFrame, index: int,
                      direction: Direction) -> float:
        """Price used to construct a collared limit; default is signal close."""
        return float(dataframe["close"].iloc[index])

    def signal_diagnostics(self, dataframe: pd.DataFrame,
                           symbol: str) -> list:
        """Structured rejected-setup diagnostics; default emits nothing."""
        return []

    def prepare_context(self, frames: dict, context: dict) -> dict:
        """Optional cross-symbol/index enrichment after per-symbol prepare.

        ``frames`` contains only tradeable symbols. ``context`` contains any
        market-context frames requested by ``context_symbols``. The default is
        deliberately a no-op so legacy strategies remain isolated.
        """
        return frames

    @property
    def context_symbols(self) -> tuple:
        return ()

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
