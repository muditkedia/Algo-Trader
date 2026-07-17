"""Scanner interface - the canonical Opportunity and the Scanner contract.

``Opportunity`` is the standard object flowing through
scanner -> ranking -> portfolio -> execution. Phase 6 extended it from the
original (symbol/strategy/confidence/rank) shape to the full canonical record:
identification, prices/risk, expectations, historical evidence, confidence
breakdown, context, costs, and the evidence reference. All new fields default
to None so producers fill what they can and consumers must tolerate gaps -
evidence-first, never fabricated.

``rank_opportunities`` remains the plain confidence ordering used when no
ranking engine is supplied; the configurable weighted ranking lives in
``algo.scanner.ranking``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Opportunity:
    """One (stock, strategy) opportunity - the canonical pipeline object."""

    # --- identification ---
    symbol: str
    strategy: str
    direction: str
    confidence: float
    timeframe: Optional[str] = None
    signal_ts: Optional[str] = None            # bar close that fired (ISO)
    # --- prices & risk ---
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    expected_risk: Optional[float] = None      # stop distance, fraction
    expected_reward: Optional[float] = None    # target excursion, fraction
    risk_reward: Optional[float] = None
    # --- expectations ---
    expected_return: Optional[float] = None    # net expectancy basis (evidence)
    expected_holding_min: Optional[float] = None
    # --- historical evidence (per strategy, from evaluations) ---
    hist_win_rate: Optional[float] = None
    hist_expectancy: Optional[float] = None
    hist_trades: Optional[int] = None
    # --- confidence detail ---
    confidence_components: Dict[str, dict] = field(default_factory=dict)
    # --- context ---
    regime: Optional[str] = None
    liquidity: Dict[str, float] = field(default_factory=dict)
    est_cost_pct: Optional[float] = None
    # --- explanation & lineage ---
    reason: str = ""
    signal_id: Optional[int] = None            # evidence reference
    # --- ranking output ---
    rank: Optional[int] = None
    rank_score: Optional[float] = None
    rank_breakdown: Dict[str, float] = field(default_factory=dict)


def rank_opportunities(opportunities: List[Opportunity]) -> List[Opportunity]:
    """Plain confidence-descending ordering (fallback when no ranking engine).

    Ties broken by expected reward. Stamps 1-based ranks.
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
