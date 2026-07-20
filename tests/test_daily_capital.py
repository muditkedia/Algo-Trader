"""Daily trading configuration: two operator inputs, everything derived.

The operator sets only::

    "capital": {"deploy_today": 300000, "max_daily_loss": 10000}

and the engine derives portfolio value (= deploy_today) and max capital per
trade (= deploy_today / 2). Position size is driven by the STRATEGY's own stop
distance and reduced until it fits every constraint at once; the RISK
constraint is the portfolio budget (tests/test_portfolio_risk.py).
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
    # These tests isolate the CAPITAL and RISK-BUDGET arithmetic, so the
    # minimum-allocation floor is switched off unless a test sets it: with
    # the floor on, a correctly risk-reduced position is skipped rather than
    # shrunk, which is the floor's behaviour and is covered separately in
    # tests/test_min_allocation.py.
    kw.setdefault("min_trade_allocation", 0.0)
    return AccountRiskEngine(RiskLimits(**kw))


# ======================================================== configuration shape

def test_operator_configures_only_two_capital_numbers():
    cfg = TradingConfig.from_dict({
        "capital": {"deploy_today": 300_000, "max_daily_loss": 10_000}})
    L = cfg.risk
    assert L.deploy_today == 300_000
    assert L.max_daily_loss == 10_000


def test_removed_fields_no_longer_exist():
    """portfolio_value / max_per_trade / risk_per_trade are NOT inputs."""
    fields = RiskLimits.__dataclass_fields__
    for gone in ("portfolio_value", "max_per_trade", "risk_per_trade",
                 "max_capital", "stake_per_trade", "max_loss_per_trade",
                 "max_exposure_pct", "daily_loss_limit"):
        assert gone not in fields, f"{gone} must not be configurable"


def test_derivations_from_deploy_today():
    L = RiskLimits(deploy_today=300_000)
    assert L.portfolio_value == 300_000        # portfolio value IS deploy_today
    assert L.max_per_trade == 150_000          # deploy_today / 2
    # risk is governed by the PORTFOLIO budget (max_daily_loss), not a
    # per-trade percentage - see tests/test_portfolio_risk.py
    assert not hasattr(L, "max_risk_per_trade")


@pytest.mark.parametrize("deploy,per_trade", [
    (300_000, 150_000), (100_000, 50_000), (1_000_000, 500_000),
])
def test_derivations_scale(deploy, per_trade):
    L = RiskLimits(deploy_today=deploy)
    assert L.max_per_trade == per_trade
    assert L.portfolio_value == deploy


def test_capital_block_overrides_risk_block():
    cfg = TradingConfig.from_dict({
        "risk": {"deploy_today": 100_000, "max_open_positions": 3},
        "capital": {"deploy_today": 300_000}})
    assert cfg.risk.deploy_today == 300_000    # operator-facing name wins
    assert cfg.risk.max_open_positions == 3    # operational limits preserved


# ============================================================ position sizing

def test_capital_cap_binds_when_the_stop_is_tight():
    """Tight stop -> risk is small, so the 50%-of-capital cap is the binder."""
    risk = _risk(deploy_today=300_000, max_daily_loss=10_000)
    # entry 100, stop 99 -> 1/share risk. capital cap 150,000 -> 1500 shares
    # (risk 1,500 <= the 10,000 budget, so capital binds)
    qty = risk.position_size(_signal(entry=100.0, stop=99.0),
                             risk_budget=10_000)
    assert qty == 1500.0
    assert qty * 100.0 <= 150_000              # A: capital per trade
    assert qty * 1.0 <= 10_000                 # B: portfolio risk budget


def test_risk_budget_binds_when_the_stop_is_wide_and_quantity_is_reduced():
    """Wide stop -> the PORTFOLIO risk budget reduces quantity below the
    capital cap (max_daily_loss 10,000 with an empty book)."""
    risk = _risk(deploy_today=300_000, max_daily_loss=10_000)
    # entry 100, stop 85 -> 15/share. The capital cap alone allows 1,500
    # shares (risk 22,500), so the 10,000 budget cuts it to 666.
    sig = _signal(entry=100.0, stop=85.0)
    qty = risk.position_size(sig, risk_budget=10_000)
    assert qty == 666.0
    assert qty * sig.risk_per_unit <= 10_000
    assert qty * 100.0 < 150_000               # capital cap not reached


def test_wider_stop_gives_a_smaller_position():
    """Sizing follows the strategy's own stop distance."""
    risk = _risk(deploy_today=300_000)
    sizes = [risk.position_size(_signal(entry=100.0, stop=s))
             for s in (85.0, 90.0, 95.0)]
    assert sizes[0] < sizes[1] < sizes[2]      # wider stop -> fewer shares


def test_available_capital_limits_the_second_position(tmp_path):
    """Capital already deployed reduces what the next trade may use."""
    risk = _risk(deploy_today=300_000)
    # only 40,000 left today -> 400 shares at 100, not the 1500 capital cap
    qty = risk.position_size(_signal(entry=100.0, stop=99.0), available=40_000)
    assert qty == 400.0


def test_quantity_is_whole_shares_and_respects_lot_size():
    risk = _risk(deploy_today=300_000)
    qty = risk.position_size(_signal(entry=2450.75, stop=2440.0))
    assert float(qty).is_integer()

    lots = AccountRiskEngine(RiskLimits(deploy_today=300_000, lot_size=25,
                                        min_trade_allocation=0.0))
    qty = lots.position_size(_signal(entry=100.0, stop=99.0))
    assert qty % 25 == 0 and qty == 1500.0     # 1500 is already a multiple
    qty2 = lots.position_size(_signal(entry=100.0, stop=90.0))
    assert qty2 % 25 == 0                      # 1200 risk-capped -> 1200


def test_no_valid_stop_is_not_tradeable():
    risk = _risk(deploy_today=300_000)
    assert risk.position_size(_signal(entry=100.0, stop=100.0)) == 0.0
    assert risk.position_size(_signal(entry=100.0, stop=105.0)) == 0.0


def test_share_more_expensive_than_the_per_trade_cap_is_refused(tmp_path):
    risk = _risk(deploy_today=100_000)          # cap 50,000/trade
    pf = PortfolioEngine(tmp_path / "p.json")
    decision = risk.check_entry(_signal(entry=60_000.0, stop=59_000.0), pf)
    assert not decision and "whole lot" in decision.reason


# ================================================= gate enforcement end-to-end

def test_entry_gate_enforces_both_caps_on_every_approved_trade(tmp_path):
    risk = _risk(deploy_today=300_000)
    pf = PortfolioEngine(tmp_path / "p.json")
    for stop in (99.0, 95.0, 90.0, 85.0, 70.0):
        sig = _signal(entry=100.0, stop=stop)
        assert risk.check_entry(sig, pf)                    # always tradeable
        qty = risk.position_size(sig)
        assert qty * sig.entry_ref <= 150_000 + 1e-6        # capital cap
        assert qty * sig.risk_per_unit <= 10_000 + 1e-6     # portfolio budget


def test_consecutive_entries_never_exceed_deploy_today(tmp_path):
    """Regression: each trade respected the per-trade cap while the SUM
    breached the daily capital, because sizing ignored what was already
    deployed. Sizing must always be against capital still available."""
    from algo.trading.models import Position
    risk = _risk(deploy_today=300_000, max_open_positions=10)
    pf = PortfolioEngine(tmp_path / "p.json")

    deployed = 0.0
    for i, symbol in enumerate(("AAA", "BBB", "CCC", "DDD")):
        sig = _signal(entry=100.0, stop=99.0, symbol=symbol)
        qty = risk.size_for(sig, pf)                 # the ONE sizing entry point
        if qty < 1:
            break
        pf.add_position(Position(
            position_id=f"{symbol}-1", symbol=symbol, strategy="orb_15m",
            timeframe="15m", quantity=qty, entry_price=100.0, entry_ts="x",
            stop=99.0, initial_stop=99.0, open_quantity=qty))
        deployed += qty * 100.0
        assert pf.deployed_capital() <= 300_000 + 1e-6, (
            f"deployed {pf.deployed_capital():,.0f} after {i + 1} positions")
    assert deployed <= 300_000 + 1e-6
    assert pf.open_count() == 2      # 150k + 150k exhausts the day's capital


def test_no_capital_left_blocks_new_entries(tmp_path):
    from algo.trading.models import Position
    risk = _risk(deploy_today=300_000, max_open_positions=10)
    pf = PortfolioEngine(tmp_path / "p.json")
    pf.add_position(Position(
        position_id="A-1", symbol="AAA", strategy="orb_15m", timeframe="15m",
        quantity=1500, entry_price=100.0, entry_ts="x", stop=99.0,
        initial_stop=99.0, open_quantity=1500))
    pf.add_position(Position(
        position_id="B-1", symbol="BBB", strategy="orb_15m", timeframe="15m",
        quantity=1500, entry_price=100.0, entry_ts="x", stop=99.0,
        initial_stop=99.0, open_quantity=1500))
    assert pf.deployed_capital() == 300_000
    decision = risk.check_entry(_signal(symbol="CCC"), pf)
    assert not decision and "no capital left" in decision.reason


# ================================================================ daily loss

def test_max_daily_loss_still_stops_new_entries_and_latches(tmp_path):
    from algo.trading.models import Position
    risk = _risk(deploy_today=300_000, max_daily_loss=10_000)
    pf = PortfolioEngine(tmp_path / "p.json")
    pos = Position(position_id="A-1", symbol="AAA", strategy="orb_15m",
                   timeframe="15m", quantity=1500, entry_price=100.0,
                   entry_ts="x", stop=99.0, initial_stop=99.0,
                   open_quantity=1500)
    pos.last_price = 93.0                       # -10,500 unrealized
    pf.add_position(pos)
    assert not risk.check_day(pf)               # halted
    assert risk.tripped
    assert not risk.check_entry(_signal(symbol="ZZZ"), pf)
    pos.last_price = 101.0                      # recovery does not un-trip
    assert not risk.check_day(pf)


def test_realized_plus_unrealized_counts_toward_the_daily_loss(tmp_path):
    risk = _risk(deploy_today=300_000, max_daily_loss=10_000)
    pf = PortfolioEngine(tmp_path / "p.json")
    pf.realized_pnl = -9_500.0                  # realized alone is under
    assert risk.check_day(pf)
    pf.realized_pnl = -10_001.0
    assert not risk.check_day(pf)
