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
from dataclasses import dataclass
from enum import Enum
from typing import Optional


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
    sell side only) - that asymmetry is captured by ``NseEquityCostModel``.
    """

    def __init__(self, per_side_pct: float = 0.0003) -> None:
        self.per_side_pct = per_side_pct

    def side_cost(self, *, price: float, quantity: float,
                  is_buy: bool, product: Product) -> float:
        return abs(price * quantity) * self.per_side_pct


@dataclass(frozen=True)
class NseCostParams:
    """Every NSE cash-equity charge, all configurable.

    Defaults are the STATUTORY rates (STT / stamp / SEBI / exchange / GST),
    which are market-wide, plus a generic discount-broker brokerage. NOTHING
    here is hardcoded to a specific broker: override ``brokerage_pct`` /
    ``brokerage_cap`` (and any statutory rate, since they change) from config.

    Rates are fractions of turnover unless named otherwise.
    """

    #: Brokerage: percentage of turnover, capped per order (both configurable).
    brokerage_pct: float = 0.0003          # 0.03%
    brokerage_cap: float = 20.0            # per order, account currency
    #: Securities Transaction Tax. Intraday: sell side only. Delivery: both.
    stt_intraday_sell: float = 0.00025     # 0.025%
    stt_delivery: float = 0.001            # 0.1%
    #: Exchange transaction charge (NSE cash).
    exchange_txn_pct: float = 0.0000297    # 0.00297%
    #: SEBI turnover fee (10 per crore).
    sebi_pct: float = 0.000001
    #: Stamp duty - BUY side only.
    stamp_intraday_buy: float = 0.00003    # 0.003%
    stamp_delivery_buy: float = 0.00015    # 0.015%
    #: GST, applied to brokerage + exchange + SEBI.
    gst_pct: float = 0.18
    #: Execution slippage assumption per side (not a statutory charge).
    slippage_pct: float = 0.0002           # 2 bps

    @classmethod
    def from_dict(cls, data) -> "NseCostParams":
        from algo.core.config import from_dict as _from_dict
        return _from_dict(cls, data)


class NseEquityCostModel(CostModel):
    """Full Indian cash-equity charge stack for one fill.

    Costs are first-order in this project: the crypto strategy died because a
    ~3 bps gross edge could never clear a ~20 bps round trip (L-006/L-009). The
    equity picture differs sharply by product - intraday round trips are far
    cheaper than delivery (STT dominates delivery) - so the product type is an
    explicit input rather than an assumption.
    """

    def __init__(self, params: Optional[NseCostParams] = None) -> None:
        self.params = params or NseCostParams()

    def breakdown(self, *, price: float, quantity: float, is_buy: bool,
                  product: Product) -> dict:
        """Per-charge breakdown for one fill (auditable against a contract note)."""
        p = self.params
        turnover = abs(price * quantity)
        brokerage = min(turnover * p.brokerage_pct, p.brokerage_cap)

        if product == Product.INTRADAY:
            stt = 0.0 if is_buy else turnover * p.stt_intraday_sell
            stamp = turnover * p.stamp_intraday_buy if is_buy else 0.0
        else:
            stt = turnover * p.stt_delivery
            stamp = turnover * p.stamp_delivery_buy if is_buy else 0.0

        exchange = turnover * p.exchange_txn_pct
        sebi = turnover * p.sebi_pct
        gst = p.gst_pct * (brokerage + exchange + sebi)
        slippage = turnover * p.slippage_pct
        total = brokerage + stt + exchange + sebi + stamp + gst + slippage
        return {"turnover": turnover, "brokerage": brokerage, "stt": stt,
                "exchange": exchange, "sebi": sebi, "stamp": stamp, "gst": gst,
                "slippage": slippage, "total": total}

    def side_cost(self, *, price: float, quantity: float,
                  is_buy: bool, product: Product) -> float:
        return self.breakdown(price=price, quantity=quantity, is_buy=is_buy,
                              product=product)["total"]
