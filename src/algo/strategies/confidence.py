"""Confidence components - the weighted-score mechanism, promoted from the
archived crypto DecisionEngine (its ``CheckResult`` + ``_conviction_score``).

A strategy's confidence is a weighted average of named component scores in
[0, 1]. The components (not just the final number) are persisted with every
signal (``signals.confidence_components``), because the crypto phase proved the
hard way that hand-designed advisory inputs can carry zero predictive value
(L-003: winner score == loser score, AUC 0.51). These Phase-4 scores are
therefore explicitly HEURISTIC signal-quality hypotheses:

  * they rank opportunities today (something must),
  * every component is recorded as evidence, and
  * the research engine later measures which components actually discriminate
    and recalibrates or zeroes their weights (the frozen confidence lifecycle).

Nothing here is a probability until the calibration layer earns that claim.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List


def clip01(value: float) -> float:
    """Clamp to [0, 1]; NaN/None-safe (maps to 0)."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(value):
        return 0.0
    return min(1.0, max(0.0, value))


@dataclass(frozen=True)
class Component:
    """One named contribution to a confidence score."""

    name: str
    score: float          # 0..1 (clipped on aggregation)
    weight: float         # relative weight, > 0
    detail: str = ""      # human-readable evidence for the score


@dataclass
class ConfidenceScore:
    """A strategy's scored opinion of the current bar."""

    score: float                                  # weighted 0..1
    components: Dict[str, dict] = field(default_factory=dict)
    reason: str = ""

    @classmethod
    def zero(cls, reason: str = "") -> "ConfidenceScore":
        return cls(score=0.0, components={}, reason=reason)


def weighted(components: List[Component], reason: str = "") -> ConfidenceScore:
    """Weighted average of component scores (the archived conviction formula:
    ``score = sum(weight * score) / sum(weight)``), with the full component
    breakdown preserved for the evidence store."""
    total = sum(c.weight for c in components)
    if total <= 0:
        return ConfidenceScore.zero(reason)
    score = sum(c.weight * clip01(c.score) for c in components) / total
    breakdown = {
        c.name: {"score": round(clip01(c.score), 4),
                 "weight": round(c.weight, 4),
                 "detail": c.detail}
        for c in components
    }
    return ConfidenceScore(score=round(clip01(score), 4),
                           components=breakdown, reason=reason)
