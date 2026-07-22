"""Declarative hypotheses - compose research ideas from reusable components.

The research bottleneck after 27 hand-written strategies is not measurement (the
frozen pipeline judges anything automatically) but EXPRESSION: each candidate was
a ~60-line class. A ``Hypothesis`` declares the same idea in a few lines from the
building blocks in ``algo.research.components``, and ``compile_hypothesis``
turns it into a real ``StrategyProfile`` that plugs into the existing seam,
measurement and frozen gate unchanged. Generating a grid of N hypotheses becomes
a loop, not N files.

This does NOT change the methodology, the gate, or any statistic. It changes how
fast an idea becomes a measurable strategy. Compiled hypotheses are constructed
on demand by a research script - they are NOT auto-discovered into the live
library, so nothing here adds a tradeable strategy or a promotion path.

    Hypothesis(
        name="mom_6_1", family="momentum",
        hypothesis="6-1 cross-sectional momentum, top decile",
        metric=components.momentum(126, 21), cross_sectional=True,
        horizon_bars=(21, 42, 63), max_hold_bars=63, min_history=160)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import pandas as pd

from algo.core.enums import Direction, HoldingScope
from algo.research.components import Condition, Metric, combine_and
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import ConfidenceScore, weighted, Component
from algo.strategies.cross_section import CrossSectionalDecileStrategy, entered


@dataclass(frozen=True)
class Hypothesis:
    """A declarative research hypothesis: signal construction + holding horizon.

    Exactly one of ``metric`` (a cross-sectional decile sort) or ``entry`` (a
    per-symbol boolean condition) defines the signal. ``filters`` are per-symbol
    conditions AND-ed with it. Everything else - measurement, the selection-edge
    gate, benchmarks - is the frozen platform.
    """

    name: str
    family: str
    hypothesis: str
    horizon_bars: Tuple[int, ...]
    max_hold_bars: int
    min_history: int
    version: str = "1.0"
    # --- signal (exactly one) ---
    metric: Optional[Metric] = None
    entry: Optional[Condition] = None
    # --- cross-sectional sort options (used with ``metric``) ---
    cross_sectional: bool = False
    rank_top: bool = True
    quantile: float = 0.10
    # --- shared ---
    filters: Tuple[Condition, ...] = ()
    direction: Direction = Direction.LONG
    holding_scope: HoldingScope = HoldingScope.SWING
    timeframe: str = "1d"

    def __post_init__(self) -> None:
        if (self.metric is None) == (self.entry is None):
            raise ValueError(f"{self.name}: set exactly one of metric / entry")
        if self.metric is not None and not self.cross_sectional:
            raise ValueError(f"{self.name}: a metric is a cross-sectional sort; "
                             "set cross_sectional=True (or use entry=)")

    def to_meta(self) -> StrategyMeta:
        return StrategyMeta(
            name=self.name, version=self.version, direction=self.direction,
            holding_scope=self.holding_scope, timeframe=self.timeframe,
            min_bars=self.min_history, required_columns=("close",),
            supported_regimes=("bull",), hypothesis=self.hypothesis,
            expected_behaviour=f"compiled hypothesis ({self.family})",
            known_failure_modes=("compiled from reusable components - the "
                                 "hypothesis, not the encoding, is under test",),
            enabled=False,            # research object, never live-discovered
            horizon_bars=tuple(self.horizon_bars),
            max_hold_bars=self.max_hold_bars)


class _CompiledCrossSectional(CrossSectionalDecileStrategy):
    """A cross-sectional decile hypothesis, compiled."""

    def __init__(self, spec: Hypothesis) -> None:
        super().__init__(settings=None)
        self._spec = spec
        self.meta = spec.to_meta()
        self.cross_sectional = True
        self.metric_col = "hyp_metric"
        self.quantile = spec.quantile
        self.top = spec.rank_top
        self._filter = combine_and(*spec.filters) if spec.filters else None

    def compute_metric(self, df: pd.DataFrame) -> pd.Series:
        return self._spec.metric(df)

    def min_history(self) -> int:
        return int(self._spec.min_history)

    def entry_signal(self, df: pd.DataFrame) -> pd.Series:
        if "xs_flag" not in df.columns or len(df) < self.min_history():
            return self.no_signal(df)
        membership = df["xs_flag"] > 0.5
        if self._filter is not None:
            membership = membership & self._filter(df)
        return entered(membership.astype(float)).fillna(False)


class _CompiledPerSymbol(StrategyProfile):
    """A per-symbol entry hypothesis, compiled."""

    def __init__(self, spec: Hypothesis) -> None:
        super().__init__(settings=None)
        self._spec = spec
        self.meta = spec.to_meta()
        self._entry = spec.entry
        self._filter = combine_and(*spec.filters) if spec.filters else None

    def min_history(self) -> int:
        return int(self._spec.min_history)

    def entry_signal(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < self.min_history():
            return self.no_signal(df)
        sig = self._entry(df).fillna(False)
        if self._filter is not None:
            sig = sig & self._filter(df)
        return sig

    def confidence(self, df: pd.DataFrame) -> ConfidenceScore:
        if df.empty:
            return ConfidenceScore.zero("empty")
        return weighted([Component(self._spec.family, 0.5, 1.0,
                                   self._spec.hypothesis)],
                        reason=self._spec.hypothesis)


def compile_hypothesis(spec: Hypothesis) -> StrategyProfile:
    """Compile a ``Hypothesis`` into a measurable ``StrategyProfile`` instance."""
    if spec.cross_sectional:
        return _CompiledCrossSectional(spec)
    return _CompiledPerSymbol(spec)


def compile_all(specs: Sequence[Hypothesis]) -> list:
    """Compile a batch (e.g. a parameter grid) into strategies for measurement."""
    return [compile_hypothesis(s) for s in specs]
