"""Scanner interface - evaluate strategies across the universe (interface only).

The scanner is where, during market hours, every enabled strategy is evaluated
against every eligible stock and the resulting candidates are scored and ranked
into opportunities. Phase 1 defines only the contract and the data shapes; there
is no broker, no market-data feed, and no live loop here - a concrete scanner is
built at the paper-trading phase.

The output ``Opportunity`` carries exactly the fields the approved design lists:
stock, strategy, direction, confidence, expected reward/risk/holding, reason,
and rank. ``rank_opportunities`` is the pure, testable ranking used by any
concrete scanner.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Opportunity:
    """One ranked (stock, strategy) opportunity emitted by a scan."""

    symbol: str
    strategy: str
    direction: str
    confidence: float
    expected_reward: Optional[float] = None
    expected_risk: Optional[float] = None
    expected_holding_min: Optional[float] = None
    reason: str = ""
    rank: Optional[int] = None


def rank_opportunities(opportunities: List[Opportunity]) -> List[Opportunity]:
    """Return opportunities sorted best-first and stamped with 1-based ranks.

    Ordering is by confidence descending; ties broken by expected reward. Pure
    and side-effect-free on the inputs' order semantics beyond setting ``rank``.
    """
    ordered = sorted(
        opportunities,
        key=lambda o: (o.confidence, o.expected_reward or 0.0),
        reverse=True,
    )
    for i, opp in enumerate(ordered, start=1):
        opp.rank = i
    return ordered


class Scanner(ABC):
    """Contract for a scan over the eligible universe at a point in time."""

    @abstractmethod
    def scan(self, as_of) -> List[Opportunity]:
        """Evaluate enabled strategies over the eligible universe at ``as_of``
        and return ranked opportunities. Implementations record every evaluated
        signal to the evidence store, whatever its disposition.
        """
