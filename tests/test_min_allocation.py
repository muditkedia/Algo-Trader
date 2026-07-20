"""Minimum trade allocation (§5): the smallest position worth taking.

``min_trade_allocation`` is a fraction of ``deploy_today`` (default 25%). A
position that cannot reach it is SKIPPED, never padded - the floor is a policy
about which trades are worth taking, not a licence to breach the per-trade cap,
the capital available, or the portfolio risk budget. Those three remain
authoritative; the floor only ever removes trades.

The interaction that matters operationally: a floor of F caps the number of
simultaneous positions at floor(1/F) regardless of ``max_open_positions``,
because each position must commit at least F of the day's capital.
"""

import pandas as pd
import pytest

from algo.execution import structural_intraday
from algo.trading.config import RiskLimits, TradingConfig
from algo.trading.portfolio import PortfolioEngine
from algo.trading.risk import AccountRiskEngine
from algo.trading.signals import TradingSignal


def _signal(entry=100.0, stop=99.0, symbol="RELIANCE", strategy="orb_15m"):
    return TradingSignal(
        symbol=symbol, strategy=strategy, timeframe="15m",
        bar_time=pd.Timestamp("2024-03-04 09:45", tz="UTC"), entry_ref=entry,
        spec=structural_intraday(stop_col="or_low"), stop=stop,
        session="2024-03-04")


def _risk(**kw):
    kw.setdefault("deploy_today", 300_000)
    kw.setdefault("max_daily_loss", 10_000)
    return AccountRiskEngine(RiskLimits(**kw))


# ============================================================ configuration

def test_default_is_25_percent_and_configurable():
    assert RiskLimits().min_trade_allocation == 0.25
    L = RiskLimits(deploy_today=300_000)
    assert L.min_per_trade == 75_000                       # 25% of 300k

    custom = RiskLimits(deploy_today=300_000, min_trade_allocation=0.10)
    assert custom.min_per_trade == 30_000                  # not a constant


def test_operator_can_set_it_through_the_capital_block():
    cfg = TradingConfig.from_dict({"capital": {
        "deploy_today": 400_000, "max_daily_loss": 10_000,
        "min_trade_allocation": 0.5}})
    assert cfg.risk.min_trade_allocation == 0.5
    assert cfg.risk.min_per_trade == 200_000


def test_zero_disables_the_floor():
    risk = _risk(min_trade_allocation=0.0)
    # 1 share of a 100-rupee stock is far below any sane floor
    qty = risk.position_size(_signal(entry=100.0, stop=99.0), available=150.0)
    assert qty == 1.0


# ================================================================ the floor

def test_a_position_below_the_floor_is_skipped_not_shrunk():
    risk = _risk(min_trade_allocation=0.25)               # floor = 75,000
    # only 40,000 of capital left: the best position is 400 shares = 40,000,
    # which is under the floor
    qty = risk.position_size(_signal(entry=100.0, stop=99.0), available=40_000)
    assert qty == 0.0


def test_a_position_at_or_above_the_floor_is_taken_unchanged():
    risk = _risk(min_trade_allocation=0.25)
    qty = risk.position_size(_signal(entry=100.0, stop=99.0), available=80_000)
    assert qty == 800.0                                   # 80,000 >= 75,000


def test_the_floor_never_enlarges_a_position():
    """The decisive property: a floor must not override a cap.

    With only 40,000 available, satisfying a 75,000 floor would mean deploying
    capital the account does not have. The trade is skipped instead.
    """
    risk = _risk(min_trade_allocation=0.25)
    for available in (10_000, 40_000, 74_999):
        qty = risk.position_size(_signal(entry=100.0, stop=99.0),
                                 available=available)
        assert qty * 100.0 <= available                   # never over-commits
        assert qty == 0.0                                 # and never padded


def test_the_floor_never_breaches_the_per_trade_cap():
    """A floor ABOVE the per-trade cap makes trading impossible - and must do
    so by refusing, not by exceeding the cap."""
    risk = _risk(min_trade_allocation=0.9)   # floor 270,000 > cap 150,000
    qty = risk.position_size(_signal(entry=100.0, stop=99.0))
    assert qty == 0.0


def test_the_risk_budget_still_binds_before_the_floor():
    """Risk remains authoritative: the floor cannot buy more shares to reach
    an allocation the day's loss budget will not support."""
    risk = _risk(min_trade_allocation=0.25, max_daily_loss=1_000)
    # 10/share of stop distance and only 1,000 of budget -> 100 shares =
    # 10,000, well under the 75,000 floor -> skipped, NOT increased
    qty = risk.position_size(_signal(entry=100.0, stop=90.0))
    assert qty == 0.0
    unfloored = risk.position_size(_signal(entry=100.0, stop=90.0),
                                   apply_minimum=False)
    assert unfloored == 100.0                # the risk cap, not the floor


# =========================================================== the entry gate

def test_entry_gate_explains_a_floor_rejection_distinctly(tmp_path):
    """'too small to be worth taking' and 'cannot afford one lot' call for
    opposite operator responses, so they must not share a message."""
    risk = _risk(min_trade_allocation=0.25)
    pf = PortfolioEngine(tmp_path / "p.json")
    pf.realized_pnl = -9_700.0               # only 300 of risk budget left
    decision = risk.check_entry(_signal(entry=100.0, stop=99.0), pf)
    assert not decision
    assert "below minimum allocation" in decision.reason
    assert "75,000" in decision.reason       # the floor is named


def test_entry_gate_still_reports_an_unaffordable_lot(tmp_path):
    risk = _risk(deploy_today=1_000, max_daily_loss=500,
                 min_trade_allocation=0.0)
    pf = PortfolioEngine(tmp_path / "p.json")
    decision = risk.check_entry(_signal(entry=5_000.0, stop=4_900.0), pf)
    assert not decision
    assert "cannot size a whole lot" in decision.reason


def test_approved_entries_still_satisfy_every_cap(tmp_path):
    risk = _risk(min_trade_allocation=0.25)
    pf = PortfolioEngine(tmp_path / "p.json")
    sig = _signal(entry=100.0, stop=99.0)
    assert risk.check_entry(sig, pf)
    qty = risk.size_for(sig, pf)
    L = risk.limits
    assert qty * sig.entry_ref >= L.min_per_trade         # floor honoured
    assert qty * sig.entry_ref <= L.max_per_trade         # cap honoured
    assert qty * sig.risk_per_unit <= L.max_daily_loss    # budget honoured


# ================================================ the constraint interaction

@pytest.mark.parametrize("allocation, concurrent", [
    (0.25, 4), (0.5, 2), (0.20, 5), (0.34, 2),
])
def test_floor_caps_concurrent_positions(allocation, concurrent):
    """Each position commits at least ``allocation`` of the day's capital, so
    at most floor(1/allocation) can be open at once - independently of
    ``max_open_positions``. The operator must be told this, because a
    max_open_positions of 5 with a 25% floor can only ever reach 4.
    """
    L = RiskLimits(deploy_today=300_000, min_trade_allocation=allocation,
                   max_open_positions=5)
    assert int(1.0 / L.min_trade_allocation) == concurrent
    assert L.min_per_trade * concurrent <= L.deploy_today
    assert L.min_per_trade * (concurrent + 1) > L.deploy_today


def test_capital_exhaustion_skips_rather_than_taking_a_token_position(tmp_path):
    """End to end: after deploying most of the day's capital, the next signal
    is refused instead of being taken in a size too small to matter."""
    risk = _risk(min_trade_allocation=0.25)
    pf = PortfolioEngine(tmp_path / "p.json")
    from algo.trading.models import Position
    pf.add_position(Position(
        position_id="p1", symbol="AAA", strategy="orb_15m", timeframe="15m",
        quantity=1_400, entry_price=200.0, entry_ts="2024-03-04T04:00:00+00:00",
        stop=199.0, initial_stop=199.0, open_quantity=1_400, last_price=200.0))
    state = risk.risk_state(pf)
    assert 0 < state.available_capital < risk.limits.min_per_trade
    decision = risk.check_entry(_signal(entry=100.0, stop=99.0, symbol="BBB"),
                                pf)
    assert not decision and "below minimum allocation" in decision.reason
