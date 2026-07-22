"""PortfolioEngine - the live portfolio state (open positions, pending orders,
P&L, daily statistics), persisted for restart safety.

Single source of truth for what the system BELIEVES it holds; recovery
reconciles this against the broker on startup. Pure bookkeeping - it never
talks to a broker or makes risk decisions. State is written atomically after
every mutation (temp file + replace) so a crash never leaves a torn file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List

from algo.core.logging import get_logger
from algo.trading.models import Order, Position, now_iso

logger = get_logger("trading.portfolio")


class PortfolioEngine:
    def __init__(self, state_file) -> None:
        self.state_file = Path(state_file)
        self.positions: Dict[str, Position] = {}     # position_id -> Position
        self.orders: Dict[str, Order] = {}           # client_order_id -> Order
        self.realized_pnl = 0.0
        self.session_date = ""
        self.closed_trades: List[dict] = []

    # ---------------------------------------------------------- lookups

    def open_positions(self) -> List[Position]:
        return [p for p in self.positions.values() if p.status != "CLOSED"]

    def open_count(self) -> int:
        return len(self.open_positions())

    def has_position(self, symbol: str, strategy: str) -> bool:
        return any(p.symbol == symbol and p.strategy == strategy
                   for p in self.open_positions())

    def has_symbol(self, symbol: str) -> bool:
        return any(p.symbol == symbol for p in self.open_positions())

    def executed_group_this_session(self, symbol: str, group: str,
                                    session: str) -> bool:
        if any(p.symbol == symbol and p.exclusive_group == group
               and p.session == session for p in self.open_positions()):
            return True
        return any(t.get("symbol") == symbol
                   and t.get("exclusive_group") == group
                   and t.get("session") == session
                   for t in self.closed_trades)

    def has_active_conflict(self, symbol: str, group: str) -> bool:
        return any(p.symbol == symbol and p.active_conflict_group == group
                   for p in self.open_positions())

    def session_blocked(self, symbol: str, groups: tuple, session: str) -> bool:
        wanted = set(groups)
        if not wanted:
            return False
        if any(p.symbol == symbol and p.session_block_group in wanted
               and p.session == session for p in self.open_positions()):
            return True
        if any(t.get("symbol") == symbol
               and t.get("session_block_group") in wanted
               and t.get("session") == session for t in self.closed_trades):
            return True
        return any(o.symbol == symbol and o.intent == "entry"
                   and o.session_block_group in wanted
                   and o.session == session for o in self.pending_orders())

    def deployed_capital(self) -> float:
        return sum(p.entry_price * p.open_quantity for p in self.open_positions())

    def unrealized_pnl(self) -> float:
        return sum(p.unrealized() for p in self.open_positions())

    def pending_orders(self) -> List[Order]:
        from algo.trading.models import TERMINAL
        return [o for o in self.orders.values() if o.status not in TERMINAL]

    # --------------------------------------------------------- mutations

    def add_position(self, position: Position) -> None:
        self.positions[position.position_id] = position
        self.persist()

    def record_order(self, order: Order) -> None:
        self.orders[order.client_order_id] = order
        self.persist()

    def mark(self, prices: Dict[str, float]) -> None:
        for p in self.open_positions():
            px = prices.get(p.symbol)
            if px is not None and px == px:
                p.last_price = float(px)

    def book_partial(self, position: Position, qty: float, price: float) -> None:
        pnl = position.sign * (price - position.entry_price) * qty
        position.realized_pnl += pnl
        position.open_quantity -= qty
        position.partial_done = True
        self.realized_pnl += pnl
        self.persist()

    def close_position(self, position: Position, price: float,
                       reason: str) -> dict:
        qty = position.open_quantity
        pnl = (position.sign * (price - position.entry_price) * qty
               + position.realized_pnl)
        position.realized_pnl = pnl
        position.open_quantity = 0.0
        position.status = "CLOSED"
        self.realized_pnl += position.sign * (price - position.entry_price) * qty
        record = {
            "position_id": position.position_id, "symbol": position.symbol,
            "strategy": position.strategy, "entry_price": position.entry_price,
            "exit_price": price, "quantity": position.quantity,
            "pnl": pnl, "exit_reason": reason, "entry_ts": position.entry_ts,
            "exit_ts": now_iso(), "session": position.session,
            "direction": position.direction,
            "exclusive_group": position.exclusive_group,
            "active_conflict_group": position.active_conflict_group,
            "session_block_group": position.session_block_group,
        }
        self.closed_trades.append(record)
        self.persist()
        return record

    def daily_stats(self) -> dict:
        wins = [t for t in self.closed_trades if t["pnl"] > 0]
        losses = [t for t in self.closed_trades if t["pnl"] < 0]
        return {
            "session": self.session_date,
            "open_positions": self.open_count(),
            "closed_trades": len(self.closed_trades),
            "wins": len(wins), "losses": len(losses),
            "realized_pnl": round(self.realized_pnl, 2),
            "unrealized_pnl": round(self.unrealized_pnl(), 2),
            "deployed_capital": round(self.deployed_capital(), 2),
        }

    # ------------------------------------------------------- persistence

    def persist(self) -> None:
        data = {
            "session_date": self.session_date,
            "realized_pnl": self.realized_pnl,
            "positions": [p.to_dict() for p in self.positions.values()],
            "orders": [o.to_dict() for o in self.orders.values()],
            "closed_trades": self.closed_trades,
            "saved_ts": now_iso(),
        }
        tmp = self.state_file.with_suffix(".tmp")
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=1, default=str),
                       encoding="utf-8")
        os.replace(tmp, self.state_file)      # atomic

    def load(self) -> bool:
        if not self.state_file.exists():
            return False
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.session_date = data.get("session_date", "")
        self.realized_pnl = float(data.get("realized_pnl", 0.0))
        self.positions = {p["position_id"]: Position.from_dict(p)
                          for p in data.get("positions", [])}
        self.orders = {o["client_order_id"]: Order.from_dict(o)
                       for o in data.get("orders", [])}
        self.closed_trades = data.get("closed_trades", [])
        logger.info("portfolio loaded: %d positions, %d orders, realized %.2f",
                    len(self.positions), len(self.orders), self.realized_pnl)
        return True

    def reset_for_new_session(self, session_date: str) -> None:
        """Fresh trading day: clear intraday realized P&L and closed trades
        (positions are squared off overnight; anything still present is handled
        by recovery)."""
        self.session_date = session_date
        self.realized_pnl = 0.0
        self.closed_trades = []
        self.persist()
