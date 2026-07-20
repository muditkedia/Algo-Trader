"""Portfolio-level risk budget: open risk, pending reservations, sizing.

The engine manages risk across the whole book, not one trade at a time:

    remaining = max_daily_loss - realized_loss - open_risk - reserved_risk

Open risk uses each position's CURRENT stop (so trailing frees budget), and
working entry orders reserve their risk until they terminate.
"""

import pandas as pd
import pytest

from algo.execution import structural_intraday
from algo.trading.config import RiskLimits
from algo.trading.models import Order, OrderStatus, Position, Side
from algo.trading.portfolio import PortfolioEngine
from algo.trading.risk import (
    AccountRiskEngine, order_reserved_risk, position_open_risk,
)
from algo.trading.signals import TradingSignal


def _risk(deploy=300_000, max_daily_loss=10_000, **kw):
    # the minimum-allocation floor is off here: these tests pin the shared
    # risk budget, and the floor's skip-vs-shrink behaviour is covered in
    # tests/test_min_allocation.py
    kw.setdefault("min_trade_allocation", 0.0)
    return AccountRiskEngine(RiskLimits(deploy_today=deploy,
                                        max_daily_loss=max_daily_loss, **kw))


def _pf(tmp_path):
    return PortfolioEngine(tmp_path / "p.json")


def _signal(entry=100.0, stop=99.0, symbol="RELIANCE", strategy="orb_15m"):
    return TradingSignal(
        symbol=symbol, strategy=strategy, timeframe="15m",
        bar_time=pd.Timestamp("2024-03-04 09:45", tz="UTC"), entry_ref=entry,
        spec=structural_intraday(stop_col="or_low"), stop=stop,
        session="2024-03-04")


def _position(symbol="AAA", entry=100.0, stop=98.0, qty=100.0,
              strategy="orb_15m"):
    return Position(
        position_id=f"{symbol}-{strategy}-1", symbol=symbol,
        strategy=strategy, timeframe="15m", quantity=qty, entry_price=entry,
        entry_ts="2024-03-04T04:00:00+00:00", stop=stop, initial_stop=stop,
        open_quantity=qty, session="2024-03-04")


def _entry_order(cid="c1", qty=100.0, risk_per_share=2.0,
                 status=OrderStatus.OPEN, filled=0.0, symbol="AAA"):
    return Order(client_order_id=cid, symbol=symbol, side=Side.BUY,
                 quantity=qty, order_type="MARKET", intent="entry",
                 status=status, filled_quantity=filled,
                 risk_per_share=risk_per_share)


# ============================================================== open risk

def test_open_risk_is_quantity_times_distance_to_current_stop():
    assert position_open_risk(_position(entry=100, stop=98, qty=100)) == 200.0


def test_open_risk_decreases_as_the_trailing_stop_tightens():
    pos = _position(entry=100, stop=95, qty=100)
    assert position_open_risk(pos) == 500.0
    pos.stop = 97.0                       # trail up
    assert position_open_risk(pos) == 300.0
    pos.stop = 99.0                       # trail further
    assert position_open_risk(pos) == 100.0


def test_breakeven_position_consumes_zero_risk():
    pos = _position(entry=100, stop=100, qty=100)
    assert position_open_risk(pos) == 0.0


def test_stop_in_profit_consumes_zero_risk_never_negative():
    pos = _position(entry=100, stop=105, qty=100)     # locked-in profit
    assert position_open_risk(pos) == 0.0             # not -500


def test_trailing_frees_budget_for_the_next_trade(tmp_path):
    risk, pf = _risk(), _pf(tmp_path)
    pos = _position(entry=100, stop=90, qty=800)      # 8,000 open risk
    pf.add_position(pos)
    assert risk.risk_state(pf).remaining == pytest.approx(2_000)
    pos.stop = 96.0                                   # trail -> 3,200 risk
    assert risk.risk_state(pf).remaining == pytest.approx(6_800)
    pos.stop = 100.0                                  # breakeven -> 0 risk
    assert risk.risk_state(pf).remaining == pytest.approx(10_000)


# ==================================================== pending reservations

def test_working_entry_order_reserves_risk_immediately(tmp_path):
    risk, pf = _risk(), _pf(tmp_path)
    pf.record_order(_entry_order(qty=100, risk_per_share=2.0))
    state = risk.risk_state(pf)
    assert state.reserved_risk == 200.0
    assert state.remaining == pytest.approx(9_800)


@pytest.mark.parametrize("terminal", [
    OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
])
def test_terminal_orders_release_their_reservation(tmp_path, terminal):
    risk, pf = _risk(), _pf(tmp_path)
    order = _entry_order(qty=100, risk_per_share=2.0)
    pf.record_order(order)
    assert risk.risk_state(pf).reserved_risk == 200.0
    order.status = terminal
    pf.record_order(order)
    assert risk.risk_state(pf).reserved_risk == 0.0
    assert risk.risk_state(pf).remaining == pytest.approx(10_000)


def test_partial_fill_splits_reserved_and_open_risk(tmp_path):
    """Filled portion becomes OPEN risk (via its position); only the
    still-working quantity stays reserved."""
    risk, pf = _risk(), _pf(tmp_path)
    order = _entry_order(qty=100, risk_per_share=2.0,
                         status=OrderStatus.PARTIALLY_FILLED, filled=40.0)
    pf.record_order(order)
    pf.add_position(_position(entry=100, stop=98, qty=40))   # 80 open risk
    state = risk.risk_state(pf)
    assert state.reserved_risk == pytest.approx(120.0)       # 60 x 2
    assert state.open_risk == pytest.approx(80.0)            # 40 x 2
    assert state.committed == pytest.approx(200.0)           # no double count


def test_exit_orders_reserve_nothing(tmp_path):
    risk, pf = _risk(), _pf(tmp_path)
    exit_order = _entry_order(cid="x", qty=100, risk_per_share=2.0)
    exit_order.intent = "exit"
    pf.record_order(exit_order)
    assert risk.risk_state(pf).reserved_risk == 0.0


def test_reservation_helper_is_never_negative():
    over_filled = _entry_order(qty=100, filled=140.0)         # defensive
    assert order_reserved_risk(over_filled) == 0.0


# ============================================================ risk budget

def test_remaining_budget_formula(tmp_path):
    risk, pf = _risk(max_daily_loss=10_000), _pf(tmp_path)
    pf.realized_pnl = -1_000.0                               # realized loss
    pf.add_position(_position(symbol="AAA", entry=100, stop=98, qty=1000))
    pf.record_order(_entry_order(qty=500, risk_per_share=1.0))
    state = risk.risk_state(pf)
    assert state.realized_loss == 1_000.0
    assert state.open_risk == 2_000.0
    assert state.reserved_risk == 500.0
    assert state.remaining == pytest.approx(10_000 - 1_000 - 2_000 - 500)


def test_realized_profit_does_not_enlarge_the_budget(tmp_path):
    risk, pf = _risk(), _pf(tmp_path)
    pf.realized_pnl = 50_000.0
    assert risk.risk_state(pf).remaining == pytest.approx(10_000)


def test_budget_floors_at_zero(tmp_path):
    risk, pf = _risk(max_daily_loss=1_000), _pf(tmp_path)
    pf.add_position(_position(entry=100, stop=90, qty=1000))   # 10,000 risk
    assert risk.risk_state(pf).remaining == 0.0                # not negative


def test_utilization_percentage(tmp_path):
    risk, pf = _risk(max_daily_loss=10_000), _pf(tmp_path)
    pf.add_position(_position(entry=100, stop=98, qty=1000))    # 2,000
    pf.record_order(_entry_order(qty=500, risk_per_share=1.0))  # 500
    assert risk.risk_state(pf).utilization_pct == pytest.approx(25.0)


# ================================================ portfolio-constrained sizing

def test_the_scenario_from_the_specification(tmp_path):
    """A: 2,000 + B: 2,500 + C: 1,500 open, 1,000 reserved -> 3,000 left.
    A strategy wanting 5,500 of risk is REDUCED to exactly 3,000."""
    risk, pf = _risk(deploy=300_000, max_daily_loss=10_000), _pf(tmp_path)
    # 100 shares each (Rs 10,000 capital apiece - capital is not the binder);
    # stops chosen to give the spec's risks: 2,000 / 2,500 / 1,500
    pf.add_position(_position(symbol="A", entry=100, stop=80, qty=100))
    pf.add_position(_position(symbol="B", entry=100, stop=75, qty=100))
    pf.add_position(_position(symbol="C", entry=100, stop=85, qty=100))
    pf.record_order(_entry_order(qty=100, risk_per_share=10.0))   # 1,000
    state = risk.risk_state(pf)
    assert state.open_risk == pytest.approx(6_000)
    assert state.reserved_risk == pytest.approx(1_000)
    assert state.remaining == pytest.approx(3_000)

    # natural (capital-bound) size is 1,500 shares at 3/share = 4,500 risk;
    # the 3,000 budget cuts it to exactly 1,000 shares = 3,000 risk
    sig = _signal(entry=100.0, stop=97.0, symbol="D")
    assert risk.position_size(sig, available=270_000) == 1500.0   # unconstrained
    qty = risk.size_for(sig, pf)                                  # constrained
    assert qty == 1000.0
    assert qty * sig.risk_per_unit == pytest.approx(3_000)     # reduced, not refused
    assert risk.check_entry(sig, pf)


def test_quantity_is_reduced_rather_than_rejected(tmp_path):
    risk, pf = _risk(max_daily_loss=10_000), _pf(tmp_path)
    pf.add_position(_position(entry=100, stop=90, qty=900))     # 9,000 risk
    sig = _signal(entry=100.0, stop=99.0, symbol="ZZZ")
    assert risk.check_entry(sig, pf)                            # still allowed
    assert risk.size_for(sig, pf) == 1000.0                     # 1,000 left


def test_rejected_only_when_one_lot_cannot_fit(tmp_path):
    risk = _risk(deploy=10_000_000, max_daily_loss=10_000)
    pf = _pf(tmp_path)
    pf.add_position(_position(entry=100, stop=99, qty=9_995))    # 9,995 risk
    sig = _signal(entry=100.0, stop=90.0, symbol="ZZZ")          # 10/share
    assert risk.size_for(sig, pf) == 0.0
    decision = risk.check_entry(sig, pf)
    assert not decision and "cannot size a whole lot" in decision.reason


def test_exhausted_budget_blocks_new_entries(tmp_path):
    risk, pf = _risk(max_daily_loss=10_000), _pf(tmp_path)
    pf.add_position(_position(entry=100, stop=90, qty=1000))     # 10,000
    decision = risk.check_entry(_signal(symbol="ZZZ"), pf)
    assert not decision and "risk budget exhausted" in decision.reason


def test_aggregate_risk_never_exceeds_max_daily_loss(tmp_path):
    """Sequentially open as many positions as the engine allows; the book's
    total risk must never breach the day's loss limit."""
    risk = _risk(deploy=10_000_000, max_daily_loss=10_000,
                 max_open_positions=50)
    pf = _pf(tmp_path)
    for i in range(30):
        sig = _signal(entry=100.0, stop=95.0, symbol=f"S{i}")
        if not risk.check_entry(sig, pf):
            break
        qty = risk.size_for(sig, pf)
        if qty < 1:
            break
        pos = _position(symbol=f"S{i}", entry=100, stop=95, qty=qty)
        pf.add_position(pos)
        state = risk.risk_state(pf)
        assert state.open_risk <= 10_000 + 1e-6, (
            f"open risk {state.open_risk} after {i + 1} positions")
    assert risk.risk_state(pf).open_risk == pytest.approx(10_000)


def test_simultaneous_pending_orders_cannot_over_allocate(tmp_path):
    """Orders submitted back-to-back (none filled yet) must collectively stay
    inside the budget - this is what the reservation system exists for."""
    risk = _risk(deploy=10_000_000, max_daily_loss=10_000,
                 max_open_positions=50)
    pf = _pf(tmp_path)
    for i in range(30):
        sig = _signal(entry=100.0, stop=95.0, symbol=f"S{i}")
        qty = risk.size_for(sig, pf)
        if qty < 1:
            break
        # order is WORKING, not filled: only the reservation exists
        pf.record_order(_entry_order(cid=f"c{i}", qty=qty, risk_per_share=5.0,
                                     status=OrderStatus.OPEN, symbol=f"S{i}"))
        assert risk.risk_state(pf).reserved_risk <= 10_000 + 1e-6
    state = risk.risk_state(pf)
    assert state.reserved_risk == pytest.approx(10_000)
    assert state.remaining == 0.0


def test_mixed_open_and_pending_respect_one_budget(tmp_path):
    risk = _risk(max_daily_loss=10_000, max_open_positions=50)
    pf = _pf(tmp_path)
    pf.add_position(_position(symbol="A", entry=100, stop=96, qty=1000))  # 4k
    pf.record_order(_entry_order(cid="c1", qty=1000, risk_per_share=3.0))  # 3k
    sig = _signal(entry=100.0, stop=95.0, symbol="C")                # 5/share
    qty = risk.size_for(sig, pf)
    assert qty * 5.0 == pytest.approx(3_000)          # only 3k left
    state = risk.risk_state(pf)
    assert state.committed + qty * 5.0 <= 10_000 + 1e-6


# ============================================ untouched existing behaviour

def test_capital_caps_still_apply(tmp_path):
    """Portfolio risk is the risk authority; capital caps are unchanged."""
    risk, pf = _risk(deploy=300_000, max_daily_loss=1_000_000), _pf(tmp_path)
    qty = risk.size_for(_signal(entry=100.0, stop=99.99), pf)
    assert qty * 100.0 <= 150_000 + 1e-6              # deploy_today / 2


def test_max_daily_loss_still_latches_and_blocks(tmp_path):
    risk, pf = _risk(max_daily_loss=1_000), _pf(tmp_path)
    pos = _position(entry=100, stop=99, qty=500)
    pos.last_price = 97.0                             # -1,500 unrealized
    pf.add_position(pos)
    assert not risk.check_day(pf)
    assert risk.tripped
    assert not risk.check_entry(_signal(symbol="ZZZ"), pf)
    pos.last_price = 101.0                            # does not un-trip
    assert not risk.check_day(pf)


def test_lot_size_still_respected_under_the_risk_cap(tmp_path):
    risk = _risk(max_daily_loss=10_000, lot_size=25)
    pf = _pf(tmp_path)
    qty = risk.size_for(_signal(entry=100.0, stop=95.0), pf)
    assert qty % 25 == 0 and qty * 5.0 <= 10_000 + 1e-6
