"""AccountRiskEngine - account-level controls, enforced BEFORE order creation.

Every entry candidate passes through ``check_entry``; the engine returns an
allow/deny decision with a reason (persisted to the event log). It also owns
the day-level circuit state (daily loss limit, error circuit breaker, kill
switch / emergency stop). It reads portfolio state but never mutates it - a
pure decision layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from algo.core.logging import get_logger
from algo.trading.config import MAX_PER_TRADE_FRACTION, RiskLimits

logger = get_logger("trading.risk")


@dataclass
class RiskDecision:
    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


@dataclass(frozen=True)
class PortfolioRisk:
    """A complete, consistent snapshot of the portfolio's risk position.

    THE single source of truth: sizing, the entry gate, the startup summary
    and the dashboard all read this one structure, so no two of them can
    disagree about how much risk is outstanding.
    """

    max_daily_loss: float
    realized_loss: float          # >= 0; only losses consume the budget
    open_risk: float              # entry -> CURRENT stop, per open position
    reserved_risk: float          # working (unfilled) entry orders
    remaining: float              # budget left for new trades
    realized_pnl: float
    unrealized_pnl: float
    deployed_capital: float
    available_capital: float
    capital_base: float = 0.0     # the day's allowance (deploy_today)

    @property
    def committed(self) -> float:
        """Risk currently committed: open positions + pending entries."""
        return self.open_risk + self.reserved_risk

    @property
    def portfolio_value(self) -> float:
        """The day's capital plus everything made or lost on it so far.

        Defined ONCE, here, because two definitions existed briefly and
        disagreed: the risk limits expose the day's ALLOWANCE
        (``RiskLimits.portfolio_value`` = deploy_today, a constant input) while
        the dashboard wanted the live MARK. Both are legitimate quantities and
        both were called "portfolio value", which is precisely how a panel ends
        up contradicting the engine.
        """
        return self.capital_base + self.realized_pnl + self.unrealized_pnl

    @property
    def utilization_pct(self) -> float:
        if self.max_daily_loss <= 0:
            return 0.0
        return 100.0 * (self.realized_loss + self.committed) / self.max_daily_loss


def position_open_risk(position) -> float:
    """Risk still carried by one open position: quantity x (entry - CURRENT
    stop), floored at zero.

    Uses the live stop, so a trailing stop reduces this automatically; at
    breakeven it is exactly zero and once the stop is in profit it stays zero
    (never negative - a winning position does not create risk budget).
    """
    sign = getattr(position, "sign", 1.0)
    distance = sign * (float(position.entry_price) - float(position.stop))
    return max(0.0, distance) * float(position.open_quantity)


def order_reserved_risk(order) -> float:
    """Risk an unfilled ENTRY order reserves: the quantity still working
    multiplied by its per-share stop distance.

    A terminal order (filled, cancelled, rejected, expired) reserves nothing,
    so reservations are released the moment the order finishes - there is no
    separate ledger to keep in sync. A partial fill automatically reserves
    only the remainder; the filled part becomes open risk via its position.
    """
    from algo.trading.models import TERMINAL
    if order.intent != "entry" or order.status in TERMINAL:
        return 0.0
    remaining = max(0.0, float(order.quantity) - float(order.filled_quantity))
    return remaining * max(0.0, float(order.risk_per_share))


class AccountRiskEngine:
    def __init__(self, limits: RiskLimits, kill_switch_file: Optional[str] = None
                 ) -> None:
        self.limits = limits
        self.kill_switch_file = kill_switch_file
        self.error_streak = 0
        self.tripped = False           # circuit breaker / emergency latched
        self.trip_reason = ""

    # ------------------------------------------------------ circuit state

    def record_error(self) -> None:
        self.error_streak += 1
        if self.error_streak >= self.limits.circuit_breaker_errors:
            self.trip(f"circuit breaker: {self.error_streak} consecutive errors")

    def record_success(self) -> None:
        self.error_streak = 0

    def trip(self, reason: str) -> None:
        if not self.tripped:
            self.tripped = True
            self.trip_reason = reason
            logger.error("RISK TRIP: %s", reason)

    def emergency_stop_requested(self) -> bool:
        if self.kill_switch_file and Path(self.kill_switch_file).exists():
            return True
        return self.tripped

    # -------------------------------------------------- portfolio risk state

    def risk_state(self, portfolio) -> PortfolioRisk:
        """Recompute the whole risk picture from CURRENT state.

        Everything is derived on demand from live positions and the order
        book, so a trailing stop that tightens, a fill, a cancellation or a
        rejection is reflected the instant it happens - nothing has to be
        notified and no counter can drift.

            remaining = max_daily_loss - realized_loss
                                       - open_risk - reserved_risk

        Realized PROFIT does not enlarge the budget (a good morning must not
        licence a reckless afternoon); only realized losses consume it.
        """
        L = self.limits
        realized_pnl = float(portfolio.realized_pnl)
        realized_loss = max(0.0, -realized_pnl)
        open_risk = sum(position_open_risk(p)
                        for p in portfolio.open_positions())
        reserved = sum(order_reserved_risk(o)
                       for o in portfolio.orders.values())
        remaining = max(0.0, L.max_daily_loss - realized_loss
                        - open_risk - reserved)
        deployed = float(portfolio.deployed_capital())
        return PortfolioRisk(
            max_daily_loss=float(L.max_daily_loss),
            realized_loss=realized_loss, open_risk=open_risk,
            reserved_risk=reserved, remaining=remaining,
            realized_pnl=realized_pnl,
            unrealized_pnl=float(portfolio.unrealized_pnl()),
            deployed_capital=deployed,
            available_capital=max(0.0, L.deploy_today - deployed),
            capital_base=float(L.deploy_today))

    def check_day(self, portfolio) -> RiskDecision:
        """Day-level gates independent of any single entry (call each tick)."""
        if self.emergency_stop_requested():
            return RiskDecision(False, self.trip_reason or "emergency stop")
        day_pnl = portfolio.realized_pnl + portfolio.unrealized_pnl()
        if day_pnl <= -abs(self.limits.max_daily_loss):
            self.trip(f"daily loss limit hit ({day_pnl:.0f} <= "
                      f"-{self.limits.max_daily_loss:.0f})")
            return RiskDecision(False, self.trip_reason)
        return RiskDecision(True)

    # ------------------------------------------------------- entry gate

    def check_entry(self, signal, portfolio) -> RiskDecision:
        """All pre-order account checks for one entry candidate."""
        day = self.check_day(portfolio)
        if not day:
            return day
        L = self.limits

        if portfolio.open_count() >= L.max_open_positions:
            return RiskDecision(False,
                                f"max_open_positions ({L.max_open_positions})")

        # duplicate-position prevention
        if portfolio.has_position(signal.symbol, signal.strategy):
            return RiskDecision(False,
                                f"duplicate: {signal.symbol}/{signal.strategy}")
        if (not L.allow_multiple_strategies_per_symbol
                and portfolio.has_symbol(signal.symbol)):
            return RiskDecision(False,
                                f"symbol already held: {signal.symbol}")
        if signal.exclusive_group and portfolio.executed_group_this_session(
                signal.symbol, signal.exclusive_group, signal.session):
            return RiskDecision(
                False, f"session suppression: {signal.symbol}/"
                       f"{signal.exclusive_group} already executed")
        if (signal.active_conflict_group
                and portfolio.has_active_conflict(
                    signal.symbol, signal.active_conflict_group)):
            return RiskDecision(
                False, f"active conflict: {signal.symbol}/"
                       f"{signal.active_conflict_group} already held")
        if (signal.blocked_by_session_groups
                and portfolio.session_blocked(
                    signal.symbol, signal.blocked_by_session_groups,
                    signal.session)):
            return RiskDecision(
                False, f"session blocker active: {signal.symbol}/"
                       f"{','.join(signal.blocked_by_session_groups)}")

        # position-sizing validation: the sizer applies every constraint, so
        # an entry is tradeable exactly when it yields a non-zero quantity
        if signal.entry_ref <= 0:
            return RiskDecision(False, "invalid entry price")
        state = self.risk_state(portfolio)
        if state.available_capital <= 0:
            return RiskDecision(
                False, f"no capital left: {state.deployed_capital:,.0f} of "
                       f"{L.deploy_today:,.0f} deployed")
        if signal.risk_per_unit <= 0:
            return RiskDecision(False, "stop is not protective for direction")
        if state.remaining <= 0:
            return RiskDecision(
                False, f"portfolio risk budget exhausted: open "
                       f"{state.open_risk:,.0f} + reserved "
                       f"{state.reserved_risk:,.0f} + realized loss "
                       f"{state.realized_loss:,.0f} of "
                       f"{L.max_daily_loss:,.0f}")

        qty = self.size_for(signal, portfolio)
        if qty < 1:
            # distinguish "too small to be worth taking" from "cannot afford
            # a single lot" - they call for opposite operator responses
            unconstrained = self.position_size(
                signal, available=state.available_capital,
                risk_budget=state.remaining, apply_minimum=False)
            if unconstrained >= 1 and L.min_per_trade > 0:
                return RiskDecision(
                    False,
                    f"below minimum allocation: the largest position the caps "
                    f"allow is {unconstrained:g} share(s) = Rs "
                    f"{unconstrained * signal.entry_ref:,.0f}, under the "
                    f"{L.min_trade_allocation:.0%} floor of Rs "
                    f"{L.min_per_trade:,.0f}")
            return RiskDecision(
                False, f"cannot size a whole lot at {signal.entry_ref:.2f}: "
                       f"stop distance {signal.risk_per_unit:.2f}/share needs "
                       f">{state.remaining:,.0f} risk budget or "
                       f">{min(L.max_per_trade, state.available_capital):,.0f} "
                       f"capital for one lot")
        # the sizer guarantees every cap; assert rather than re-deciding, so
        # the two paths can never drift apart silently
        assert signal.risk_per_unit * qty <= state.remaining + 1e-6
        strategy_cap = (signal.spec.max_capital_per_trade
                        or MAX_PER_TRADE_FRACTION)
        assert qty * signal.entry_ref <= min(
            L.max_per_trade, L.deploy_today * strategy_cap) + 1e-6
        return RiskDecision(True)

    def size_for(self, signal, portfolio) -> float:
        """The quantity to trade given the portfolio's CURRENT state.

        The single entry point every caller must use. It reads capital already
        deployed AND the remaining portfolio risk budget, so a series of
        trades can neither over-commit capital nor collectively carry more
        risk than the day's loss limit allows.
        """
        state = self.risk_state(portfolio)
        return self.position_size(signal, available=state.available_capital,
                                  risk_budget=state.remaining)

    def position_size(self, signal, available: Optional[float] = None,
                      risk_budget: Optional[float] = None,
                      apply_minimum: bool = True) -> float:
        """Final quantity for an entry, satisfying EVERY constraint at once.

        The strategy's own stop (from its frozen ExecutionSpec) drives the
        risk arithmetic - a wider stop yields a smaller position - and the
        quantity is then reduced until it fits inside all of:

          1. **max capital per trade** - deploy_today / 2,
          2. **capital still available** today,
          3. **the remaining PORTFOLIO risk budget** - what is left of
             max_daily_loss after realized losses, the open risk of existing
             positions, and risk reserved by working entry orders,
          4. **exchange lot size** and whole shares (NSE cash equity = 1).

        Whichever constraint binds first wins, so the quantity is automatically
        reduced rather than the entry being refused; it is refused only when
        the result rounds down to nothing. Strategy logic is not involved: it
        supplies the entry and the stop, nothing more.
        """
        L = self.limits
        entry = float(signal.entry_ref)
        if entry <= 0:
            return 0.0
        available = L.deploy_today if available is None else float(available)

        # capital constraints -> a share count
        strategy_cap = getattr(signal.spec, "max_capital_per_trade", None)
        capital_allowed = min(
            L.max_per_trade,
            L.deploy_today * strategy_cap if strategy_cap is not None
            else L.max_per_trade,
            max(available, 0.0))
        qty = capital_allowed / entry

        # portfolio risk constraint: (entry - stop) x qty <= remaining budget
        risk_per_share = float(signal.risk_per_unit)
        if risk_per_share <= 0:
            return 0.0            # no valid stop -> not tradeable
        budget = (L.max_daily_loss if risk_budget is None
                  else max(0.0, float(risk_budget)))
        strategy_risk = getattr(signal.spec, "risk_per_trade_pct", None)
        if strategy_risk is not None:
            budget = min(budget, L.deploy_today * strategy_risk)
        qty = min(qty, budget / risk_per_share)

        # The specification applies the confidence grade after both base caps.
        qty *= max(0.0, min(1.0, float(signal.grade_multiplier)))

        # exchange lot size + whole shares
        lot = max(int(L.lot_size or 1), 1)
        qty = float((int(qty) // lot) * lot)
        if qty <= 0:
            return 0.0

        # MINIMUM ALLOCATION - applied last, and it never enlarges a position.
        #
        # The floor is a policy about which trades are worth taking, not a
        # licence to exceed a limit: a position that cannot reach
        # ``min_per_trade`` WITHOUT breaching the per-trade cap, the capital
        # still available, or the remaining portfolio risk budget is skipped,
        # never padded. Sizing up to satisfy a minimum would let the softest
        # constraint override the hardest one, which is exactly backwards.
        # An explicit strategy allocation model (such as STRAT-01's 20% cap
        # with 50/75% grade scaling) is authoritative and must not be rejected
        # by the generic 25% "worth taking" floor.
        floor = 0.0 if strategy_cap is not None else float(L.min_per_trade)
        if apply_minimum and floor > 0 and qty * entry < floor:
            return 0.0
        return qty
