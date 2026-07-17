"""Net break-even protection - a winner must never become a NET loser.

Naive "move the stop to entry" is a trap: exiting at the entry price loses the
full round trip (brokerage + STT + exchange + GST + stamp + SEBI + slippage).
The true break-even is the entry price GROSSED UP by every charge the round
trip will incur, plus a small execution buffer:

    breakeven_price = entry x (1 + round_trip_cost_pct + buffer_pct)

The cost comes from the configured ``CostModel`` (the same NSE model the
research and paper engines price with), so it is never a guessed number and it
automatically differs for INTRADAY vs DELIVERY.

Timing matters as much as level. Moving to break-even immediately after entry
strangles trades before they can work (the crypto phase's tight-trail
post-mortem: raising the stop too early cut winners short - L-006). So the
protection ARMS only once a configurable profit trigger is met, expressed as a
multiple of the round-trip cost (default: 3x cost) and optionally an ATR
multiple - whichever the owner configures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from algo.core.config import from_dict
from algo.core.costs import CostModel, Product


@dataclass(frozen=True)
class BreakevenConfig:
    """When to arm net break-even, and how much cushion to leave."""

    enabled: bool = True
    #: ARM once unrealized profit >= this multiple of the round-trip cost.
    #: (default 3x: the trade has clearly paid for itself before we tighten)
    arm_at_cost_multiple: float = 3.0
    #: optional additional/alternative trigger: profit >= this many ATRs.
    #: None disables it. When both are set, BOTH must be satisfied.
    arm_at_atr_multiple: Optional[float] = None
    #: execution buffer above true break-even (fraction of price), covering
    #: fill slippage on the stop itself.
    buffer_pct: float = 0.0005

    @classmethod
    def from_dict(cls, data) -> "BreakevenConfig":
        return from_dict(cls, data)


def round_trip_cost_pct(entry_price: float, cost_model: CostModel,
                        product: Product, quantity: Optional[float] = None,
                        reference_stake: float = 100_000.0) -> float:
    """Round-trip cost as a fraction of the entry notional.

    Priced at the entry price on both legs - the correct basis for a stop
    placed AT break-even (the exit is, by construction, near that price).
    """
    if entry_price <= 0:
        return 0.0
    qty = quantity if quantity else max(reference_stake / entry_price, 1e-9)
    return cost_model.round_trip_pct(entry_price=entry_price,
                                     exit_price=entry_price, quantity=qty,
                                     product=product)


def breakeven_stop_price(entry_price: float, cost_model: CostModel,
                         product: Product, config: BreakevenConfig,
                         quantity: Optional[float] = None) -> float:
    """The price at which exiting nets exactly zero (plus the buffer)."""
    cost = round_trip_cost_pct(entry_price, cost_model, product, quantity)
    return entry_price * (1.0 + cost + config.buffer_pct)


def should_arm(entry_price: float, current_price: float,
               cost_model: CostModel, product: Product,
               config: BreakevenConfig, atr: Optional[float] = None,
               quantity: Optional[float] = None) -> bool:
    """Has the trade earned enough to protect? (never immediately after entry)"""
    if not config.enabled or entry_price <= 0:
        return False
    profit_pct = current_price / entry_price - 1.0
    cost = round_trip_cost_pct(entry_price, cost_model, product, quantity)
    if profit_pct < config.arm_at_cost_multiple * cost:
        return False
    if config.arm_at_atr_multiple is not None:
        if not atr or atr <= 0:
            return False
        if (current_price - entry_price) < config.arm_at_atr_multiple * atr:
            return False
    return True


def breakeven_candidate(entry_price: float, current_price: float,
                        cost_model: CostModel, product: Product,
                        config: BreakevenConfig, atr: Optional[float] = None,
                        quantity: Optional[float] = None) -> Optional[float]:
    """The net-break-even stop price once armed, else None.

    Returned as a CANDIDATE: the caller ratchets it against the existing stop
    (stops only ever tighten), exactly like the trailing candidates.
    """
    if not should_arm(entry_price, current_price, cost_model, product, config,
                      atr, quantity):
        return None
    return breakeven_stop_price(entry_price, cost_model, product, config,
                                quantity)
