"""Phase 7 Part A production execution rules:
max-3 positions, capital pool, broker buying power, net break-even, trails."""

import numpy as np
import pandas as pd
import pytest

from algo.core.costs import NseCostParams, NseEquityCostModel, Product
from algo.core.enums import Direction, HoldingScope
from algo.evidence.database import EvidenceDB, MEMORY
from algo.evidence.logger import EvidenceLogger
from algo.paper.buying_power import (
    BuyingPowerConfig, from_rms, resolve_buying_power,
)
from algo.paper.engine import PaperEngine
from algo.paper.portfolio import Action, PortfolioConfig, PortfolioManager
from algo.risk.breakeven import (
    BreakevenConfig, breakeven_candidate, breakeven_stop_price, should_arm,
)
from algo.risk.engine import RiskParams
from algo.risk.trailing import TrailConfig, trail_stop_price
from algo.strategies.base import StrategyMeta, StrategyProfile

COSTS = NseEquityCostModel()
FREE = NseEquityCostModel(NseCostParams(
    brokerage_pct=0, brokerage_cap=0, stt_intraday_sell=0, stt_delivery=0,
    exchange_txn_pct=0, sebi_pct=0, stamp_intraday_buy=0, stamp_delivery_buy=0,
    gst_pct=0, slippage_pct=0))


# ------------------------------------------------- A3: buying power (no 5x)

def test_buying_power_cash_mode_has_no_leverage():
    cfg = BuyingPowerConfig(mode="cash")
    assert resolve_buying_power(1_000_000, cfg, Product.INTRADAY) == 1_000_000


def test_buying_power_multiplier_is_configured_not_hardcoded():
    # the OWNER supplies the allowance; nothing in code assumes 5x
    cfg = BuyingPowerConfig(mode="multiplier", intraday_multiplier=5.0,
                            delivery_multiplier=1.0)
    assert resolve_buying_power(200_000, cfg, Product.INTRADAY) == 1_000_000
    assert resolve_buying_power(200_000, cfg, Product.DELIVERY) == 200_000
    # a different broker/allowance flows straight through
    cfg2 = BuyingPowerConfig(mode="multiplier", intraday_multiplier=2.5)
    assert resolve_buying_power(200_000, cfg2, Product.INTRADAY) == 500_000


def test_buying_power_from_broker_rms():
    rms = {"net": "150000", "availablecash": "120000",
           "availableintradaypayin": "480000", "utiliseddebits": "0"}
    cfg = BuyingPowerConfig(mode="broker")
    assert resolve_buying_power(200_000, cfg, Product.INTRADAY,
                                rms=rms) == 480_000     # intraday field wins
    assert resolve_buying_power(200_000, cfg, Product.DELIVERY,
                                rms=rms) == 120_000     # cash for delivery
    assert from_rms(rms, ("missing", "net")) == 150_000  # ordered fallback


def test_buying_power_broker_falls_back_and_is_ceilinged():
    cfg = BuyingPowerConfig(mode="broker", fallback_mode="cash")
    assert resolve_buying_power(100_000, cfg, Product.INTRADAY,
                                rms={}) == 100_000       # nothing usable
    # a mis-parsed enormous field cannot silently 100x risk
    guarded = BuyingPowerConfig(mode="broker", max_multiplier=3.0)
    assert resolve_buying_power(100_000, guarded, Product.INTRADAY,
                                rms={"availablecash": "99999999"}) == 300_000


# ---------------------------------------- A4: net break-even protection

def test_breakeven_price_covers_every_charge_not_just_entry():
    entry = 1000.0
    price = breakeven_stop_price(entry, COSTS, Product.INTRADAY,
                                 BreakevenConfig(buffer_pct=0.0005))
    assert price > entry                       # never plain "entry"
    # exiting AT the breakeven price must net >= 0
    qty = 100_000 / entry
    cost = COSTS.round_trip_pct(entry_price=entry, exit_price=price,
                                quantity=qty, product=Product.INTRADAY)
    assert (price / entry - 1.0) - cost >= 0
    # delivery costs more -> its breakeven sits higher
    delivery = breakeven_stop_price(entry, COSTS, Product.DELIVERY,
                                    BreakevenConfig())
    assert delivery > price


def test_breakeven_does_not_arm_immediately_after_entry():
    cfg = BreakevenConfig(arm_at_cost_multiple=3.0)
    entry = 1000.0
    assert not should_arm(entry, entry, COSTS, Product.INTRADAY, cfg)
    assert not should_arm(entry, entry * 1.001, COSTS, Product.INTRADAY, cfg)
    assert breakeven_candidate(entry, entry * 1.001, COSTS, Product.INTRADAY,
                               cfg) is None
    # ~12 bps round trip -> arms around +36 bps
    assert should_arm(entry, entry * 1.01, COSTS, Product.INTRADAY, cfg)
    assert breakeven_candidate(entry, entry * 1.01, COSTS, Product.INTRADAY,
                               cfg) is not None


def test_breakeven_trigger_is_configurable():
    entry = 1000.0
    eager = BreakevenConfig(arm_at_cost_multiple=1.0)
    patient = BreakevenConfig(arm_at_cost_multiple=20.0)
    price = entry * 1.005
    assert should_arm(entry, price, COSTS, Product.INTRADAY, eager)
    assert not should_arm(entry, price, COSTS, Product.INTRADAY, patient)
    # optional ATR condition must ALSO be satisfied when configured
    both = BreakevenConfig(arm_at_cost_multiple=1.0, arm_at_atr_multiple=2.0)
    assert not should_arm(entry, price, COSTS, Product.INTRADAY, both, atr=10.0)
    assert should_arm(entry, entry * 1.03, COSTS, Product.INTRADAY, both,
                      atr=10.0)


def test_breakeven_disabled_never_arms():
    assert breakeven_candidate(1000.0, 1100.0, COSTS, Product.INTRADAY,
                               BreakevenConfig(enabled=False)) is None


# --------------------------------------------------- A5: trailing modes

def _bars(closes, lows=None, atrs=None):
    closes = np.asarray(closes, dtype=float)
    frame = pd.DataFrame({
        "date": pd.date_range("2024-03-04 09:15", periods=len(closes),
                              freq="15min", tz="UTC"),
        "open": closes, "high": closes + 1.0,
        "low": np.asarray(lows) if lows is not None else closes - 1.0,
        "close": closes, "volume": np.full(len(closes), 100.0)})
    if atrs is not None:
        frame["atr"] = np.asarray(atrs, dtype=float)
    return frame


RISK = RiskParams(profit_lock_tiers=())      # isolate the trail from the ladder


def test_atr_mode_is_the_promoted_engine_behaviour():
    candidate = trail_stop_price(100.0, 110.0, 0.10, atr=2.0,
                                 config=TrailConfig(mode="atr",
                                                    atr_multiplier=2.0),
                                 risk=RISK)
    assert candidate == pytest.approx(106.0)     # 110 - 2*2


def test_percentage_mode():
    candidate = trail_stop_price(100.0, 110.0, 0.10, atr=2.0,
                                 config=TrailConfig(mode="percentage",
                                                    percent=0.03),
                                 risk=RISK)
    assert candidate == pytest.approx(106.7)     # 110 * 0.97


def test_structure_mode_uses_recent_swing_low():
    bars = _bars([100, 104, 103, 108, 110], lows=[99, 102, 101, 106, 108])
    candidate = trail_stop_price(
        100.0, 110.0, 0.10, atr=2.0,
        config=TrailConfig(mode="structure", structure_lookback=3,
                           structure_buffer_pct=0.0),
        risk=RISK, bars=bars)
    assert candidate == pytest.approx(101.0)     # min low of last 3 bars


def test_volatility_mode_widens_when_volatile_tightens_when_calm():
    calm = _bars([100] * 20 + [110], atrs=[2.0] * 20 + [2.0])
    spike = _bars([100] * 20 + [110], atrs=[1.0] * 20 + [3.0])
    cfg = TrailConfig(mode="volatility", atr_multiplier=2.0,
                      vol_min_multiplier=1.5, vol_max_multiplier=3.5)
    calm_stop = trail_stop_price(100.0, 110.0, 0.10, 2.0, cfg, RISK, bars=calm)
    spike_stop = trail_stop_price(100.0, 110.0, 0.10, 3.0, cfg, RISK,
                                  bars=spike)
    # unusual volatility -> wider trail (lower stop), relative to its own ATR
    assert (110.0 - spike_stop) / 3.0 > (110.0 - calm_stop) / 2.0


def test_trail_not_active_before_activation_profit():
    assert trail_stop_price(100.0, 100.2, 0.002, 2.0,
                            TrailConfig(activation_profit=0.006), RISK) is None


def test_profit_lock_ladder_applies_in_every_mode():
    risk = RiskParams(profit_lock_tiers=((0.01, 0.005),))
    for mode in ("atr", "percentage", "structure", "volatility"):
        candidate = trail_stop_price(
            100.0, 101.5, 0.015, atr=50.0,           # huge ATR -> trail useless
            config=TrailConfig(mode=mode), risk=risk, bars=_bars([100, 101.5]))
        assert candidate == pytest.approx(100.5)     # the tier still protects


def test_unknown_mode_falls_back_to_atr():
    assert trail_stop_price(100.0, 110.0, 0.10, 2.0,
                            TrailConfig(mode="nonsense"), RISK) \
        == pytest.approx(106.0)


# ------------------------------------- A1/A2: max-3 + capital pool (engine)

class AlwaysLong(StrategyProfile):
    meta = StrategyMeta(name="rules_test", version="1.0",
                        direction=Direction.LONG,
                        holding_scope=HoldingScope.INTRADAY, timeframe="15m",
                        min_bars=3, required_columns=("close",), enabled=True)

    def entry_signal(self, df):
        return pd.Series(True, index=df.index)


def _session_bars(day, n, closes=None, lows=None):
    dates = pd.date_range(f"{day} 09:15", periods=n, freq="15min",
                          tz="Asia/Kolkata").tz_convert("UTC")
    closes = np.asarray(closes if closes is not None
                        else np.linspace(100, 101, n))
    return pd.DataFrame({"date": dates, "open": closes, "high": closes + 0.5,
                         "low": np.asarray(lows) if lows is not None
                         else closes - 0.5,
                         "close": closes, "volume": np.full(n, 1000.0)})


@pytest.fixture
def engine_env(store, tmp_path):
    db = EvidenceDB(MEMORY)
    log = EvidenceLogger(db)
    symbols = [f"EQ{i}" for i in range(6)]
    for sym in symbols:
        log.upsert_instrument(sym)
        store.write(sym, "15m", _session_bars("2024-03-04", 6))
    sid = log.register_strategy("rules_test", "1.0")
    log.set_strategy_status(sid, "measured", "test PASS")
    yield store, db, log, symbols, tmp_path
    db.close()


def test_never_more_than_three_open_positions(engine_env):
    store, db, log, symbols, tmp_path = engine_env
    engine = PaperEngine(store, [AlwaysLong()], log,
                         state_path=tmp_path / "p.json")
    assert engine.portfolio_cfg.max_open_positions == 3      # production rule
    result = engine.cycle(as_of="2024-03-04 05:00+00:00", symbols=symbols)
    assert len(result["opened"]) == 3
    assert len(engine.positions) == 3
    # a fourth opportunity is decided against the book, never just added
    assert any(d.endswith(":skip") for d in result["decisions"])


def _deploy(store, log, tmp_path, symbols, bp_config, name):
    """Run one cycle under a buying-power config; return capital deployed."""
    from algo.paper.sizing import SizingConfig
    engine = PaperEngine(
        store, [AlwaysLong()], log,
        # generous daily risk budget so the POOL, not the budget, is the binding
        # constraint - this test is about buying power
        portfolio=PortfolioConfig(total_capital=300_000, daily_risk_budget=0.5),
        # risk settings that WANT more notional than the cash balance allows
        sizing=SizingConfig(risk_per_trade=0.03, max_stake=1_000_000,
                            max_capital_per_trade=1.0),
        buying_power=bp_config, state_path=tmp_path / f"{name}.json")
    engine._risk_spent = 0.0
    engine.cycle(as_of="2024-03-04 05:00+00:00", symbols=symbols)
    return engine, engine.capital_deployed()


def test_pool_is_buying_power_not_cash(engine_env):
    store, db, log, symbols, tmp_path = engine_env
    cash_engine, cash_deployed = _deploy(
        store, log, tmp_path, symbols,
        BuyingPowerConfig(mode="cash"), "cash")
    assert cash_engine.buying_power(Product.INTRADAY) == 300_000
    # cash account: deployment cannot exceed the cash balance
    assert cash_deployed <= 300_000 + 1


def test_configured_buying_power_relaxes_the_ceiling(engine_env):
    store, db, log, symbols, tmp_path = engine_env
    levered, deployed = _deploy(
        store, log, tmp_path, symbols,
        BuyingPowerConfig(mode="multiplier", intraday_multiplier=4.0), "lev")
    assert levered.buying_power(Product.INTRADAY) == 1_200_000
    # the SAME risk settings now deploy beyond cash - the pool, not the cash
    # balance, is the constraint (Phase 7 A2/A3)
    assert deployed > 300_000
    assert deployed <= 1_200_000                      # never beyond the pool
    assert levered.available_capital(Product.INTRADAY) == \
        pytest.approx(1_200_000 - deployed)


def test_risk_stays_equity_based_under_leverage(engine_env):
    """Leverage must relax the NOTIONAL ceiling, never inflate risk-per-trade:
    a stop-out loses equity, and equity does not grow with buying power."""
    from algo.paper.sizing import SizingConfig
    cfg = SizingConfig(risk_per_trade=0.01, max_stake=1e9,
                       max_capital_per_trade=1.0)
    from algo.paper.sizing import size_position
    cash = size_position(capital=300_000, available_capital=300_000,
                         pool=300_000, stop_pct=0.02, confidence=1.0,
                         risk_budget_left=1e9, config=cfg)
    levered = size_position(capital=300_000, available_capital=1_200_000,
                            pool=1_200_000, stop_pct=0.02, confidence=1.0,
                            risk_budget_left=1e9, config=cfg)
    assert cash == levered == pytest.approx(150_000)   # 1% of 300k / 2% stop
    # equity risk is identical; only the CEILING differed
    assert cash * 0.02 == pytest.approx(3_000)


def test_replace_decision_persisted_with_reasoning():
    manager = PortfolioManager(PortfolioConfig(max_open_positions=3,
                                               replace_min_improvement=0.1))
    from algo.paper.portfolio import PortfolioState
    from algo.scanner.base import Opportunity
    state = PortfolioState(open_positions=3,
                           position_scores={"A": 0.3, "B": 0.6, "C": 0.7},
                           position_sectors={"A": None, "B": None, "C": None})
    better = Opportunity(symbol="D", strategy="s", direction="long",
                         confidence=0.9)
    better.rank_score = 0.55
    decision = manager.evaluate(better, state, 500_000)
    assert decision.action == Action.REPLACE
    assert decision.replace_symbol == "A"          # weakest replaced
    assert "beats weakest" in decision.reason      # reasoning captured


def test_breakeven_and_trail_ratchet_in_the_engine(engine_env):
    store, db, log, symbols, tmp_path = engine_env
    engine = PaperEngine(
        store, [AlwaysLong()], log,
        breakeven=BreakevenConfig(arm_at_cost_multiple=2.0, buffer_pct=0.0005),
        state_path=tmp_path / "p.json")
    engine.cycle(as_of="2024-03-04 05:00+00:00", symbols=["EQ0"])
    position = engine.positions["EQ0"]
    entry, original_stop = position.entry_price, position.stop_price
    assert original_stop < entry                    # initial risk stop

    # a strong rally must arm NET break-even: stop moves ABOVE entry
    rally = _session_bars("2024-03-04", 10,
                          closes=list(np.linspace(100, 101, 6)) + [104, 105,
                                                                   106, 107])
    store.write("EQ0", "15m", rally)
    engine.manage(pd.Timestamp("2024-03-04 06:30+00:00"))
    position = engine.positions["EQ0"]
    assert position.stop_price > original_stop      # ratcheted, never widened
    assert position.stop_price > entry              # protects NET, not just entry
    assert position.breakeven_armed or position.trailed
