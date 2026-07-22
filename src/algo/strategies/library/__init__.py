"""Production strategy library - the researched candidates.

Long-only Indian-equity strategies, each a self-contained plug-in on the
Phase-1 ``StrategyProfile`` interface: indicator preparation, vectorized
edge-triggered entry signal, frozen configurable parameters, component-based
confidence, a declared regime hypothesis, a PRE-REGISTERED research horizon,
and explanation metadata.

Per the frozen research-first lifecycle these ship as CANDIDATES: they are
scannable and every signal they emit is recorded to evidence, but none is
"approved" until the research engine measures its edge against costs (D-007
gate). Their confidence scores are heuristics to be recalibrated from evidence.

**Adding a strategy: drop ONE module into this package.** It is discovered at
import and flows into ``ALL_STRATEGIES``, which is what measurement, evidence,
league tables, promotion and paper trading all read - so there is no
registration list to edit and none to forget. Until Phase 8 this file carried a
hand-maintained tuple that every one of those consumers depended on.
"""

from __future__ import annotations

import sys

from algo.strategies.registry import StrategyRegistry

#: One registry owns discovery for this package. ``register()`` raises on a
#: duplicate strategy name, so uniqueness is now enforced at import time rather
#: than by a test someone has to remember to update.
REGISTRY = StrategyRegistry()
REGISTRY.discover(sys.modules[__name__])

#: Every strategy class in the package, ordered by name. The order is
#: deterministic on purpose: a sweep's execution order must not depend on
#: filesystem or import order (reproducibility, VALIDATION_RULES SS23).
ALL_STRATEGIES = tuple(REGISTRY.get(name) for name in REGISTRY.names())

# Re-export each class under its own name so ``from algo.strategies.library
# import OpeningRangeBreakout`` keeps working. Binding these from discovery
# rather than from a list of import statements is the point: a new strategy
# module needs no edit here at all.
globals().update({cls.__name__: cls for cls in ALL_STRATEGIES})

__all__ = ([cls.__name__ for cls in ALL_STRATEGIES]
           + ["ALL_STRATEGIES", "REGISTRY"])
