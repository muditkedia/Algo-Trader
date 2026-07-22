"""OrderManager - broker-independent order lifecycle with idempotency & retry.

Owns creating/modifying/cancelling orders through the ExecutionAdapter and
recording each intent to the portfolio + event log BEFORE and AFTER the broker
call. The client_order_id is a deterministic idempotency key derived from
(position, intent, sequence), so a retry after an ambiguous failure can be
reconciled instead of duplicated. It performs bounded retries on transient
BrokerErrors; a persistent failure is surfaced to the risk engine (error
streak -> circuit breaker).
"""

from __future__ import annotations

import hashlib
import time

from algo.core.logging import get_logger
from algo.trading.adapters.base import BrokerError
from algo.trading.eventlog import EventLog
from algo.trading.models import Order, OrderStatus, Side, now_iso

logger = get_logger("trading.orders")


def client_order_id(position_id: str, intent: str, seq: int) -> str:
    raw = f"{position_id}:{intent}:{seq}"
    return "CO-" + hashlib.sha1(raw.encode()).hexdigest()[:16]


class OrderManager:
    def __init__(self, adapter, portfolio, events: EventLog,
                 risk=None, max_retries: int = 2, retry_delay: float = 0.5,
                 sleep_fn=time.sleep) -> None:
        self.adapter = adapter
        self.portfolio = portfolio
        self.events = events
        self.risk = risk
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.sleep = sleep_fn

    # --------------------------------------------------------- placement

    def _submit(self, order: Order) -> Order:
        """Place with bounded retry + idempotent recovery on ambiguity."""
        # idempotency: if we already have a non-terminal record for this id,
        # reconcile rather than resubmit
        existing = self.portfolio.orders.get(order.client_order_id)
        if existing is not None and existing.broker_order_id:
            return self.adapter.order_status(existing)

        order.created_ts = order.created_ts or now_iso()
        self.portfolio.record_order(order)          # persist intent FIRST
        attempt = 0
        while True:
            try:
                placed = self.adapter.place(order)
                self.portfolio.record_order(placed)
                self.events.emit("order", intent=order.intent,
                                 symbol=order.symbol, side=order.side,
                                 qty=order.quantity, cid=order.client_order_id,
                                 status=placed.status,
                                 broker_id=placed.broker_order_id)
                if self.risk:
                    self.risk.record_success()
                return placed
            except BrokerError as exc:
                attempt += 1
                logger.warning("order attempt %d failed (%s): %s",
                               attempt, order.client_order_id, exc)
                if attempt > self.max_retries:
                    order.status = OrderStatus.REJECTED
                    order.reason = str(exc)
                    self.portfolio.record_order(order)
                    self.events.emit("error", where="place_order",
                                     cid=order.client_order_id, detail=str(exc))
                    if self.risk:
                        self.risk.record_error()
                    raise
                self.sleep(self.retry_delay)
                # before retrying, ask the broker whether the prior attempt
                # actually landed (idempotent recovery)
                try:
                    checked = self.adapter.order_status(order)
                    if checked.broker_order_id:
                        return checked
                except BrokerError:
                    pass

    def market_entry(self, signal, quantity: float, position_id: str) -> Order:
        # ``risk_per_share`` lets the portfolio risk engine RESERVE this
        # order's risk from the moment it is recorded (before the broker even
        # acknowledges it), so orders submitted close together cannot
        # collectively exceed the day's risk budget. It is released
        # automatically when the order reaches a terminal state.
        order = Order(
            client_order_id=client_order_id(position_id, "entry", 0),
            symbol=signal.symbol, side=Side.BUY, quantity=quantity,
            order_type="MARKET", strategy=signal.strategy, intent="entry",
            position_id=position_id,
            risk_per_share=max(0.0, float(signal.risk_per_unit)))
        return self._submit(order)

    def market_exit(self, position, quantity: float, reason: str,
                    seq: int = 0) -> Order:
        intent = "partial" if reason == "partial" else "exit"
        order = Order(
            client_order_id=client_order_id(position.position_id,
                                             f"{intent}-{reason}", seq),
            symbol=position.symbol, side=Side.SELL, quantity=quantity,
            order_type="MARKET", strategy=position.strategy, intent=intent,
            position_id=position.position_id, reason=reason)
        return self._submit(order)

    def cancel(self, order: Order) -> Order:
        try:
            cancelled = self.adapter.cancel(order)
            self.portfolio.record_order(cancelled)
            self.events.emit("order", intent="cancel", symbol=order.symbol,
                             cid=order.client_order_id)
            return cancelled
        except BrokerError as exc:
            self.events.emit("error", where="cancel_order",
                             cid=order.client_order_id, detail=str(exc))
            raise
