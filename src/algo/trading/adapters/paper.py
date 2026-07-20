"""PaperBroker - simulated execution against real market prices.

Fills are deterministic and slippage-aware: a MARKET order fills at the
adapter's current quote for the symbol, moved by ``paper_slippage_pct`` on the
side that hurts (buy up, sell down). The adapter is a PURE fill simulator - it
holds no exit logic; the TradeManager (shared with live) decides WHEN and at
what reference price to exit and sets the quote accordingly, so paper fills
reproduce the verified backtest semantics (honest gap fills, stop-first) while
the code path stays identical to live.

State is intentionally in-memory: on restart, recovery rebuilds positions from
the persisted portfolio (the paper "broker of record" is the state file), the
mirror of how live recovery rebuilds from the real broker.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from algo.core.logging import get_logger
from algo.trading.adapters.base import BrokerError, ExecutionAdapter
from algo.trading.models import (
    Fill, Order, OrderStatus, Side, now_iso,
)

logger = get_logger("trading.paper")


class PaperBroker(ExecutionAdapter):
    name = "paper"
    is_live = False

    def __init__(self, config, clock=None) -> None:
        self.config = config
        self.clock = clock
        self.slippage = float(config.paper_slippage_pct)
        self._quotes: Dict[str, float] = {}
        self._orders: Dict[str, Order] = {}
        self._positions: Dict[str, dict] = {}   # symbol -> {quantity, avg}
        self._pending_fills: List[Fill] = []

    # ------------------------------------------------------------- lifecycle

    def connect(self) -> None:
        logger.info("PaperBroker ready (slippage=%.4f%%)", self.slippage * 100)

    def update_quotes(self, prices: Dict[str, float]) -> None:
        """Feed the latest marks (the TradeManager also sets a symbol's quote
        to the intended honest fill price just before an exit order)."""
        for sym, px in prices.items():
            if px is not None and px == px:      # skip NaN
                self._quotes[sym] = float(px)

    def quote(self, symbol: str) -> Optional[float]:
        return self._quotes.get(symbol)

    # --------------------------------------------------------------- orders

    def place(self, order: Order) -> Order:
        # idempotency: re-placing a known client id returns the stored order
        existing = self._orders.get(order.client_order_id)
        if existing is not None:
            return existing
        px = self._quotes.get(order.symbol)
        if px is None:
            order.status = OrderStatus.REJECTED
            order.reason = "no paper quote for symbol"
            self._orders[order.client_order_id] = order
            raise BrokerError(order.reason)
        order.broker_order_id = f"PAPER-{len(self._orders) + 1}"
        # MARKET and triggered STOP fill immediately at the mark +/- slippage;
        # LIMIT fills only if marketable (kept simple - the manager uses market
        # exits, matching the backtest's fill convention)
        fill_px = px * (1 + self.slippage) if order.side == Side.BUY \
            else px * (1 - self.slippage)
        order.status = OrderStatus.FILLED
        order.filled_quantity = order.quantity
        order.avg_fill_price = fill_px
        order.updated_ts = order.created_ts = now_iso()
        self._orders[order.client_order_id] = order
        self._apply_fill(order, fill_px)
        self._pending_fills.append(Fill(
            client_order_id=order.client_order_id, symbol=order.symbol,
            side=order.side, quantity=order.quantity, price=fill_px,
            ts=order.updated_ts, broker_order_id=order.broker_order_id,
            intent=order.intent))
        return order

    def _apply_fill(self, order: Order, price: float) -> None:
        pos = self._positions.setdefault(
            order.symbol, {"quantity": 0.0, "avg": 0.0})
        signed = order.quantity if order.side == Side.BUY else -order.quantity
        new_qty = pos["quantity"] + signed
        if pos["quantity"] == 0 or (pos["quantity"] > 0) == (signed > 0):
            # opening/adding: weighted average
            total = abs(pos["quantity"]) + order.quantity
            pos["avg"] = ((pos["avg"] * abs(pos["quantity"])
                           + price * order.quantity) / total) if total else 0.0
        pos["quantity"] = new_qty
        if abs(new_qty) < 1e-9:
            pos["quantity"] = 0.0

    def modify(self, order: Order, *, limit_price=None,
               trigger_price=None) -> Order:
        stored = self._orders.get(order.client_order_id, order)
        if limit_price is not None:
            stored.limit_price = limit_price
        if trigger_price is not None:
            stored.trigger_price = trigger_price
        stored.updated_ts = now_iso()
        return stored

    def cancel(self, order: Order) -> Order:
        stored = self._orders.get(order.client_order_id, order)
        if stored.status not in (OrderStatus.FILLED, OrderStatus.REJECTED):
            stored.status = OrderStatus.CANCELLED
            stored.updated_ts = now_iso()
        return stored

    def order_status(self, order: Order) -> Order:
        return self._orders.get(order.client_order_id, order)

    def open_orders(self) -> List[Order]:
        return [o for o in self._orders.values()
                if o.status in (OrderStatus.OPEN, OrderStatus.PENDING,
                                OrderStatus.PARTIALLY_FILLED)]

    def positions(self) -> List[dict]:
        return [{"symbol": s, "quantity": p["quantity"], "avg_price": p["avg"]}
                for s, p in self._positions.items() if p["quantity"] != 0.0]

    def poll_fills(self) -> List[Fill]:
        fills, self._pending_fills = self._pending_fills, []
        return fills
