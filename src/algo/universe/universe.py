"""Universe orchestrator - chain filters and report what survived and why.

The universe is a composable pipeline: filters are applied in order to a frame
of candidate symbols, and each dropped symbol is attributed to the first filter
that rejected it. That attribution is recorded (it explains the eligible set to
the daily report and to the evidence trail), matching the platform principle
that every decision - including exclusions - is auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import pandas as pd

from algo.core.logging import get_logger
from algo.universe.base import Filter

logger = get_logger("universe")


@dataclass
class UniverseResult:
    """Outcome of applying the filter pipeline to a candidate frame."""

    kept: List[str]
    dropped: Dict[str, str]           # symbol -> name of the first failing filter
    per_filter_drops: Dict[str, int] = field(default_factory=dict)

    @property
    def n_in(self) -> int:
        return len(self.kept) + len(self.dropped)

    @property
    def n_kept(self) -> int:
        return len(self.kept)


class Universe:
    """An ordered pipeline of universe filters."""

    def __init__(self, filters: List[Filter]) -> None:
        self.filters = list(filters)

    def apply(self, frame: pd.DataFrame) -> UniverseResult:
        """Reduce ``frame`` (indexed by symbol) to the eligible symbols.

        A symbol is attributed to the FIRST filter that drops it, so the reason
        is deterministic and the per-filter drop counts sum meaningfully.
        """
        remaining = list(frame.index)
        dropped: Dict[str, str] = {}
        per_filter: Dict[str, int] = {}

        for filt in self.filters:
            if not remaining:
                break
            sub = frame.loc[remaining]
            passed = filt.mask(sub).astype(bool)
            failed = [sym for sym in remaining if not passed.get(sym, False)]
            for sym in failed:
                dropped[sym] = filt.name
            per_filter[filt.name] = len(failed)
            remaining = [sym for sym in remaining if passed.get(sym, False)]

        result = UniverseResult(kept=remaining, dropped=dropped,
                                per_filter_drops=per_filter)
        logger.info("universe: %d in -> %d eligible (%d dropped)",
                    result.n_in, result.n_kept, len(dropped))
        return result
