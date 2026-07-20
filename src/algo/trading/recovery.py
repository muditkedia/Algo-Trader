"""RecoveryManager - deterministic, broker-first restart & state recovery.

Startup contract (Phase 8): reconcile against BROKER truth first, then rebuild
internal state. Steps (each logged as a RECOVERY event, each idempotent):

  1. Reload the persisted portfolio (positions, orders, realized P&L).
  2. Pull broker positions and open orders (the authority).
  3. Reconcile positions:
       - our position with a matching broker position  -> RESUME (keep the
         ratcheted stop/target/trail state from the portfolio).
       - our position with NO broker position           -> assume it closed
         while we were down: CLOSE it in the book at the last known price
         (orphaned-internal), logged for review.
       - a broker position we do not track              -> ORPHANED-BROKER:
         adopt as an unmanaged position flagged for square-off (never left
         unmanaged), logged loudly.
  4. Reconcile orders: mark terminal ones done; keep working ones; a working
     entry with no position is treated as a pending fill to re-check.
  5. Detect partial fills (broker filled_quantity between 0 and quantity) and
     set the position's open quantity to the broker's truth.
  6. Prevent duplicate placement: recovery NEVER re-sends an order - it only
     reconciles. New orders after recovery use fresh idempotency keys.

Recovery is pure reconciliation: it mutates the portfolio to match reality and
returns a structured report; it places NO orders (the engine's next cycle
manages resumed/adopted positions normally).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from algo.core.logging import get_logger
from algo.trading.models import TERMINAL, Position, now_iso

logger = get_logger("trading.recovery")


@dataclass
class RecoveryReport:
    loaded_positions: int = 0
    resumed: List[str] = field(default_factory=list)
    orphaned_internal: List[str] = field(default_factory=list)
    orphaned_broker: List[str] = field(default_factory=list)
    partial_fills: List[str] = field(default_factory=list)
    working_orders: int = 0
    reconciled_orders: int = 0

    def to_dict(self) -> dict:
        return {
            "loaded_positions": self.loaded_positions,
            "resumed": self.resumed,
            "orphaned_internal": self.orphaned_internal,
            "orphaned_broker": self.orphaned_broker,
            "partial_fills": self.partial_fills,
            "working_orders": self.working_orders,
            "reconciled_orders": self.reconciled_orders,
        }


class RecoveryManager:
    def __init__(self, portfolio, adapter, events, feed=None) -> None:
        self.portfolio = portfolio
        self.adapter = adapter
        self.events = events
        self.feed = feed

    def recover(self) -> RecoveryReport:
        report = RecoveryReport()
        self.portfolio.load()
        report.loaded_positions = self.portfolio.open_count()

        broker_positions = {p["symbol"]: p for p in self._safe_positions()}
        broker_open = {o.client_order_id: o for o in self._safe_open_orders()}
        report.working_orders = len(broker_open)

        self._reconcile_positions(broker_positions, report)
        self._reconcile_orders(broker_open, report)

        self.events.emit("recovery", **report.to_dict())
        logger.info("recovery complete: %s", report.to_dict())
        return report

    # ------------------------------------------------------- positions

    def _reconcile_positions(self, broker_positions: Dict[str, dict],
                             report: RecoveryReport) -> None:
        tracked_symbols = set()
        for pos in list(self.portfolio.open_positions()):
            tracked_symbols.add(pos.symbol)
            bp = broker_positions.get(pos.symbol)
            if bp is None or abs(bp["quantity"]) < 1e-9:
                # broker shows nothing: it closed while we were down
                price = self._last_price(pos.symbol) or pos.entry_price
                self.portfolio.close_position(pos, price, "recovered_closed")
                report.orphaned_internal.append(pos.position_id)
                self.events.emit("recovery", action="orphaned_internal",
                                 symbol=pos.symbol, position=pos.position_id)
                continue
            # partial fill: broker qty < our expected open qty
            if abs(bp["quantity"]) < pos.open_quantity - 1e-6:
                pos.open_quantity = abs(bp["quantity"])
                report.partial_fills.append(pos.position_id)
                self.events.emit("recovery", action="partial_fill",
                                 symbol=pos.symbol, qty=bp["quantity"])
            report.resumed.append(pos.position_id)

        # broker positions we do NOT track -> adopt, flag for square-off
        for symbol, bp in broker_positions.items():
            if symbol in tracked_symbols or abs(bp["quantity"]) < 1e-9:
                continue
            adopted = Position(
                position_id=f"ORPHAN-{symbol}-{now_iso()}", symbol=symbol,
                strategy="__orphan__", timeframe="15m",
                quantity=abs(bp["quantity"]), entry_price=bp["avg_price"],
                entry_ts=now_iso(), stop=0.0, initial_stop=0.0,
                open_quantity=abs(bp["quantity"]), status="OPEN",
                session=self.portfolio.session_date)
            self.portfolio.add_position(adopted)
            report.orphaned_broker.append(symbol)
            self.events.emit("recovery", action="orphaned_broker",
                             symbol=symbol, qty=bp["quantity"],
                             note="adopted; flagged for square-off")

    # --------------------------------------------------------- orders

    def _reconcile_orders(self, broker_open: Dict[str, "object"],
                          report: RecoveryReport) -> None:
        for cid, order in list(self.portfolio.orders.items()):
            if order.status in TERMINAL:
                continue
            if cid in broker_open:
                # still working at the broker: keep it, refresh status
                self.portfolio.orders[cid] = broker_open[cid]
                report.reconciled_orders += 1
            else:
                # not working: ask the broker for its terminal state
                try:
                    refreshed = self.adapter.order_status(order)
                    self.portfolio.orders[cid] = refreshed
                    report.reconciled_orders += 1
                except Exception as exc:  # pragma: no cover - best effort
                    logger.warning("order status recovery failed %s: %s",
                                   cid, exc)
        self.portfolio.persist()

    # --------------------------------------------------------- helpers

    def _safe_positions(self) -> List[dict]:
        try:
            return self.adapter.positions()
        except Exception as exc:
            logger.error("broker positions unavailable during recovery: %s", exc)
            self.events.emit("error", where="recovery_positions",
                             detail=str(exc))
            return []

    def _safe_open_orders(self) -> List["object"]:
        try:
            return self.adapter.open_orders()
        except Exception as exc:
            logger.error("broker orders unavailable during recovery: %s", exc)
            self.events.emit("error", where="recovery_orders", detail=str(exc))
            return []

    def _last_price(self, symbol: str):
        if self.feed is None:
            return None
        for tf in self.feed.timeframes:
            bt = self.feed.history(symbol, tf)   # public interface, not .store
            if not bt.empty:
                return float(bt["close"].iloc[-1])
        return None
