"""Shared domain models for the production system (broker-independent).

These are the types every stage passes around; NOTHING here knows whether the
active adapter is paper or live. Enums are plain strings so they serialize
straight into the event log and state files.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

import pandas as pd


class Side:
    BUY = "BUY"
    SELL = "SELL"


class OrderType:
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"          # stop-market (trigger)


class OrderStatus:
    PENDING = "PENDING"        # created locally, not yet acknowledged
    OPEN = "OPEN"              # acknowledged/working at the broker
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"        # day order that lapsed unfilled


#: An order in a TERMINAL state can never fill again, so it reserves no
#: portfolio risk (see algo.trading.risk.reserved_risk).
TERMINAL = frozenset({OrderStatus.FILLED, OrderStatus.CANCELLED,
                      OrderStatus.REJECTED, OrderStatus.EXPIRED})


@dataclass
class Order:
    """A broker-independent order. ``client_order_id`` is the idempotency key -
    the same intent always carries the same id, so a retry after an ambiguous
    failure can be reconciled against the broker rather than duplicated."""

    client_order_id: str
    symbol: str
    side: str
    quantity: float
    order_type: str
    product: str = "INTRADAY"        # MIS
    limit_price: Optional[float] = None
    trigger_price: Optional[float] = None
    #: linkage for reconciliation and lifecycle
    strategy: str = ""
    intent: str = ""                 # entry | exit | partial | squareoff
    position_id: str = ""
    #: Risk per share this order would create if it filled (entry - stop).
    #: Set on ENTRY orders so the portfolio risk engine can RESERVE their risk
    #: while they are working; it is accounting metadata only and never
    #: influences how the order is placed or executed.
    risk_per_share: float = 0.0
    #: broker-assigned id (None until acknowledged)
    broker_order_id: Optional[str] = None
    status: str = OrderStatus.PENDING
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    created_ts: str = ""
    updated_ts: str = ""
    reason: str = ""
    direction: str = "long"
    timeframe: str = ""
    signal_bar_time: str = ""
    session: str = ""
    entry_stop: float = 0.0
    entry_target: Optional[float] = None
    entry_target2: Optional[float] = None
    entry_timeout_target: Optional[float] = None
    partial_fraction: float = 0.0
    trail_mode: str = "none"
    atr_at_entry: float = 0.0
    structural_stop: float = 0.0
    exclusive_group: str = ""
    active_conflict_group: str = ""
    entry_target_r: float = 0.0
    session_block_group: str = ""
    timed_block_group: str = ""
    timed_block_until: str = ""
    last_managed_bar: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Order":
        return cls(**{k: v for k, v in data.items()
                      if k in cls.__dataclass_fields__})


@dataclass
class Fill:
    client_order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    ts: str
    broker_order_id: Optional[str] = None
    intent: str = ""


@dataclass
class Position:
    """A managed directional position and its live exit geometry. The
    ``ExecutionSpec`` that owns it is referenced by strategy name; the live
    TradeManager loads the spec and updates ``stop``/``target`` exactly as the
    backtest engine would."""

    position_id: str
    symbol: str
    strategy: str
    timeframe: str
    quantity: float
    entry_price: float
    entry_ts: str
    stop: float
    initial_stop: float
    target: Optional[float] = None
    target2: Optional[float] = None
    timeout_target: Optional[float] = None
    partial_fraction: float = 0.0
    partial_done: bool = False
    trail_mode: str = "none"          # none | chandelier | column
    atr_at_entry: float = 0.0
    open_quantity: float = 0.0
    realized_pnl: float = 0.0
    session: str = ""
    status: str = "OPEN"              # OPEN | CLOSING | CLOSED
    trailed: bool = False
    last_price: float = 0.0
    direction: str = "long"
    bars_held: int = 0
    highest_since_entry: float = 0.0
    lowest_since_entry: float = 0.0
    exclusive_group: str = ""
    active_conflict_group: str = ""
    session_block_group: str = ""
    timed_block_group: str = ""
    timed_block_until: str = ""
    last_managed_bar: str = ""

    def __post_init__(self) -> None:
        if not self.open_quantity:
            self.open_quantity = self.quantity
        if not self.highest_since_entry:
            self.highest_since_entry = self.entry_price
        if not self.lowest_since_entry:
            self.lowest_since_entry = self.entry_price

    @property
    def is_long(self) -> bool:
        return self.direction != "short"

    @property
    def sign(self) -> float:
        return 1.0 if self.is_long else -1.0

    def unrealized(self, price: Optional[float] = None) -> float:
        px = price if price is not None else (self.last_price or self.entry_price)
        return self.sign * (px - self.entry_price) * self.open_quantity

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        return cls(**{k: v for k, v in data.items()
                      if k in cls.__dataclass_fields__})


def now_iso() -> str:
    return pd.Timestamp.now(tz="UTC").isoformat()
