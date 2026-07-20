"""ExecutionAdapter - the broker contract (paper and live implement it).

The order manager and recovery talk ONLY to this interface. It is deliberately
small and broker-agnostic: place/modify/cancel, order status, and the two
reconciliation reads (open orders, positions) plus a quote for marking. Every
method is idempotent-friendly - ``place`` takes the client order id so an
adapter can dedupe, and ``reconcile_*`` let recovery rebuild from broker truth.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from algo.trading.models import Fill, Order


class BrokerError(RuntimeError):
    """Any adapter-level failure (network, auth, rejection)."""


class ExecutionAdapter(ABC):
    #: "paper" | "live" - for logging and the live-arming guard
    name: str = "base"
    is_live: bool = False

    @abstractmethod
    def connect(self) -> None:
        """Establish/verify the broker connection (login for live; no-op paper)."""

    @abstractmethod
    def place(self, order: Order) -> Order:
        """Submit ``order``; return it updated with broker id/status. Must be
        idempotent on ``client_order_id`` where the broker supports it."""

    @abstractmethod
    def modify(self, order: Order, *, limit_price: Optional[float] = None,
               trigger_price: Optional[float] = None) -> Order:
        """Modify a working order (used to move stops)."""

    @abstractmethod
    def cancel(self, order: Order) -> Order:
        """Cancel a working order."""

    @abstractmethod
    def order_status(self, order: Order) -> Order:
        """Refresh one order's status/fills from the broker."""

    @abstractmethod
    def open_orders(self) -> List[Order]:
        """All currently working orders at the broker (reconciliation read)."""

    @abstractmethod
    def positions(self) -> List[dict]:
        """Broker net positions: [{'symbol','quantity','avg_price'}...]
        (reconciliation read)."""

    @abstractmethod
    def quote(self, symbol: str) -> Optional[float]:
        """Last traded price for marking (may be None if unavailable)."""

    def poll_fills(self) -> List[Fill]:
        """Fills since the last poll (paper simulates; live derives from the
        order/trade book). Default: none."""
        return []

    def keepalive(self) -> None:
        """Periodic liveness upkeep (live: refresh the session JWT). Default:
        no-op (paper needs none)."""

    def disconnect(self) -> None:
        """Best-effort teardown."""
