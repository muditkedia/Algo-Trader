"""Transaction-cost model interface (framework + a flat default).

Costs are first-order in this project: the crypto strategy died because a ~3 bps
gross edge could never clear a ~20 bps round-trip fee (docs/LEARNINGS.md L-006,
L-009). The research engine therefore compares every edge against a real cost
model, and every backtest/paper fill is priced through one.

Phase 1 delivers the ``CostModel`` interface plus ``FlatCostModel`` (a simple
bps-per-side model, market-agnostic). The full Indian equity charge stack -
brokerage + STT + exchange + GST + stamp + SEBI, differing for INTRADAY vs
DELIVERY - is a concrete ``CostModel`` implemented in Phase 2, where it will be
reconciled against real broker contract notes. Nothing here hard-codes those
Indian specifics; it only fixes the shape every cost model must satisfy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum


class Product(str, Enum):
    """Position product type - drives which charges apply (Phase 2)."""

    INTRADAY = "intraday"   # MIS: squared off same session
    DELIVERY = "delivery"   # CNC: settled, held overnight+


class CostModel(ABC):
    """Contract for pricing transaction costs.

    ``side_cost`` returns the absolute cost (in account currency) of a single
    fill; ``round_trip_pct`` returns the entry+exit cost as a fraction of the
    entry notional, which is what the research engine hurdles an edge against.
    """

    @abstractmethod
    def side_cost(self, *, price: float, quantity: float,
                  is_buy: bool, product: Product) -> float:
        """Absolute cost of one fill (one side of a trade)."""

    def round_trip_pct(self, *, entry_price: float, exit_price: float,
                       quantity: float, product: Product) -> float:
        """Entry+exit cost as a fraction of entry notional."""
        notional = entry_price * quantity
        if notional <= 0:
            return 0.0
        buy = self.side_cost(price=entry_price, quantity=quantity,
                             is_buy=True, product=product)
        sell = self.side_cost(price=exit_price, quantity=quantity,
                              is_buy=False, product=product)
        return (buy + sell) / notional


class FlatCostModel(CostModel):
    """Simple symmetric bps-per-side model.

    Market-agnostic placeholder default. ``per_side_pct`` is a fraction, e.g.
    0.0003 = 3 bps/side. Real intraday equity costs are asymmetric (STT on the
    sell side only) - that asymmetry is captured by the concrete NSE model in
    Phase 2, not here.
    """

    def __init__(self, per_side_pct: float = 0.0003) -> None:
        self.per_side_pct = per_side_pct

    def side_cost(self, *, price: float, quantity: float,
                  is_buy: bool, product: Product) -> float:
        return abs(price * quantity) * self.per_side_pct
