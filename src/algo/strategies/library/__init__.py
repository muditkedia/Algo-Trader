"""Production strategy library - the Phase-4 researched candidates.

Six long-only Indian-equity strategies, each a self-contained plug-in on the
Phase-1 ``StrategyProfile`` interface: indicator preparation, vectorized
edge-triggered entry signal, frozen configurable parameters, component-based
confidence, declared regime hypothesis, and explanation metadata.

Per the frozen research-first lifecycle these ship as CANDIDATES: they are
scannable and every signal they emit is recorded to evidence, but none is
"approved" until the research engine measures its edge against costs (D-007
gate). Their confidence scores are heuristics to be recalibrated from evidence.

Discoverable via ``StrategyRegistry.discover("algo.strategies.library")``.
"""

from algo.strategies.library.pullback_15m import PullbackContinuation15m
from algo.strategies.library.volexp_1h import VolatilityExpansionBreakout1h
from algo.strategies.library.orb_15m import OpeningRangeBreakout
from algo.strategies.library.vwap_15m import VwapTrendContinuation
from algo.strategies.library.nr7_daily import Nr7VolatilityContraction
from algo.strategies.library.ema200_daily import Ema200PullbackTrend

ALL_STRATEGIES = (
    PullbackContinuation15m,
    VolatilityExpansionBreakout1h,
    OpeningRangeBreakout,
    VwapTrendContinuation,
    Nr7VolatilityContraction,
    Ema200PullbackTrend,
)

__all__ = [cls.__name__ for cls in ALL_STRATEGIES] + ["ALL_STRATEGIES"]
