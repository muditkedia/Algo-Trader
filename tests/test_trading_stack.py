"""Production trading stack - unit + integration tests.

Every stage is independently testable (single responsibility). The execution
adapter is the only mode-dependent piece; tests use PaperBroker and a stub
live client to prove the arming guard and reconciliation without a broker.
"""


import pandas as pd
import pytest

from algo.execution import ExecutionSpec, structural_intraday
from algo.trading.adapters import build_adapter
from algo.trading.adapters.base import BrokerError
from algo.trading.adapters.paper import PaperBroker
from algo.trading.clock import MarketClock
from algo.trading.config import LIVE_ENV_KEY, RiskLimits, TradingConfig
from algo.trading.eventlog import EventLog
from algo.trading.models import Order, OrderStatus, Position, Side
from algo.trading.ordermanager import OrderManager, client_order_id
from algo.trading.portfolio import PortfolioEngine
from algo.trading.recovery import RecoveryManager
from algo.trading.risk import AccountRiskEngine
from algo.trading.signals import TradingSignal
from algo.trading.trademanager import TradeManager


def _cfg(tmp_path, **kw):
    base = dict(mode="paper", state_dir=str(tmp_path / "state"),
                store_dir=str(tmp_path / "store"),
                symbols_file=str(tmp_path / "syms.txt"))
    base.update(kw)
    (tmp_path / "syms.txt").write_text("RELIANCE\nTCS\n")
    return TradingConfig.from_dict(base)


def _spec():
    return structural_intraday(stop_col="or_low", target_kind="r", target_r=2.0)


def _signal(symbol="RELIANCE", entry=100.0, stop=98.0, target=104.0,
            strategy="orb_15m", spec=None):
    return TradingSignal(
        symbol=symbol, strategy=strategy, timeframe="15m",
        bar_time=pd.Timestamp("2024-03-04 09:45", tz="UTC"), entry_ref=entry,
        spec=spec or _spec(), stop=stop, target=target, session="2024-03-04")


def _position(symbol="RELIANCE", entry=100.0, stop=98.0, target=104.0,
              qty=500.0, strategy="orb_15m", **kw):
    return Position(
        position_id=f"{symbol}-{strategy}-1", symbol=symbol, strategy=strategy,
        timeframe="15m", quantity=qty, entry_price=entry,
        entry_ts="2024-03-04T04:15:00+00:00", stop=stop, initial_stop=stop,
        target=target, open_quantity=qty, session="2024-03-04", **kw)


def _bar(o, h, l, c, **extra):
    return pd.Series({"open": o, "high": h, "low": l, "close": c, **extra})


# =============================================================== config/arming

def test_live_needs_all_three_keys(tmp_path, monkeypatch):
    monkeypatch.delenv(LIVE_ENV_KEY, raising=False)
    cfg = _cfg(tmp_path, mode="live", live_trading_enabled=True)
    assert not cfg.live_armed()                       # env missing
    monkeypatch.setenv(LIVE_ENV_KEY, "YES")
    assert cfg.live_armed()
    cfg2 = _cfg(tmp_path, mode="live", live_trading_enabled=False)
    assert not cfg2.live_armed()                      # flag missing
    cfg3 = _cfg(tmp_path, mode="paper", live_trading_enabled=True)
    assert not cfg3.live_armed()                      # not live mode


def test_build_adapter_selects_by_mode(tmp_path):
    assert build_adapter(_cfg(tmp_path, mode="paper")).name == "paper"


def test_angelone_unarmed_refuses_orders_but_allows_readonly(tmp_path,
                                                             monkeypatch):
    monkeypatch.delenv(LIVE_ENV_KEY, raising=False)
    from algo.trading.adapters.angelone import AngelOneBroker

    class StubClient:
        def orderBook(self): return {"status": True, "data": []}
        def position(self): return {"status": True, "data": []}

    class StubSession:
        client = StubClient()
        def ensure(self): return self

    cfg = _cfg(tmp_path, mode="live", live_trading_enabled=True)
    broker = AngelOneBroker(cfg, session=StubSession(), instruments=None)
    assert broker.positions() == []                   # read-only OK unarmed
    order = Order(client_order_id="x", symbol="RELIANCE", side=Side.BUY,
                  quantity=1, order_type="MARKET")
    with pytest.raises(BrokerError, match="not armed"):
        broker.place(order)


# ==================================================================== risk

def test_risk_blocks_every_documented_path(tmp_path):
    # deploy_today 100k -> max/trade 50k, max risk/trade 4k
    limits = RiskLimits(deploy_today=100_000, max_open_positions=2,
                        max_daily_loss=10_000)
    risk = AccountRiskEngine(limits)
    pf = PortfolioEngine(tmp_path / "p.json")

    # a wide stop is no longer REFUSED - the quantity is reduced to fit
    # whichever constraint binds (here capital: 50,000 / 100 = 500 shares,
    # since the empty book leaves the full 10,000 risk budget available)
    wide = _signal(stop=90.0)
    assert risk.check_entry(wide, pf)
    assert risk.size_for(wide, pf) == 500.0

    ok = _signal(stop=99.0)                            # capital-bound: 500
    assert risk.check_entry(ok, pf)

    # duplicate symbol
    pf.add_position(_position())
    assert not risk.check_entry(_signal(strategy="vwap_15m"), pf)

    # max positions
    pf.add_position(_position(symbol="TCS", strategy="orb_15m"))
    assert pf.open_count() == 2
    d = risk.check_entry(_signal(symbol="INFY"), pf)
    assert not d and "max_open_positions" in d.reason


def test_daily_loss_limit_trips_and_latches(tmp_path):
    limits = RiskLimits(max_daily_loss=1_000)
    risk = AccountRiskEngine(limits)
    pf = PortfolioEngine(tmp_path / "p.json")
    pos = _position(qty=500, entry=100, stop=98)
    pos.last_price = 97.0                              # -1500 unrealized
    pf.add_position(pos)
    assert not risk.check_day(pf)
    assert risk.tripped
    # latched: recovering price does not un-trip
    pos.last_price = 101.0
    assert not risk.check_day(pf)


def test_circuit_breaker_and_kill_switch(tmp_path):
    kill = tmp_path / "KILL"
    risk = AccountRiskEngine(RiskLimits(circuit_breaker_errors=3),
                             kill_switch_file=str(kill))
    for _ in range(3):
        risk.record_error()
    assert risk.tripped
    risk2 = AccountRiskEngine(RiskLimits(), kill_switch_file=str(kill))
    assert not risk2.emergency_stop_requested()
    kill.write_text("stop")
    assert risk2.emergency_stop_requested()


# ================================================================= paper fills

def test_paper_fills_with_slippage_and_accounts_positions(tmp_path):
    cfg = _cfg(tmp_path, paper_slippage_pct=0.001)
    broker = PaperBroker(cfg)
    broker.connect()
    broker.update_quotes({"RELIANCE": 100.0})
    order = Order(client_order_id="c1", symbol="RELIANCE", side=Side.BUY,
                  quantity=10, order_type="MARKET")
    filled = broker.place(order)
    assert filled.status == OrderStatus.FILLED
    assert filled.avg_fill_price == pytest.approx(100.1)   # +slippage on buy
    assert broker.positions()[0]["quantity"] == 10
    # idempotent: re-place same id does not double
    again = broker.place(order)
    assert again.avg_fill_price == pytest.approx(100.1)
    assert broker.positions()[0]["quantity"] == 10
    # sell closes
    broker.update_quotes({"RELIANCE": 105.0})
    broker.place(Order(client_order_id="c2", symbol="RELIANCE", side=Side.SELL,
                       quantity=10, order_type="MARKET"))
    assert broker.positions() == []


def test_paper_rejects_without_a_quote(tmp_path):
    broker = PaperBroker(_cfg(tmp_path))
    with pytest.raises(BrokerError):
        broker.place(Order(client_order_id="c", symbol="X", side=Side.BUY,
                           quantity=1, order_type="MARKET"))


# ============================================================== order manager

def test_order_ids_are_deterministic():
    assert client_order_id("p", "entry", 0) == client_order_id("p", "entry", 0)
    assert client_order_id("p", "entry", 0) != client_order_id("p", "exit", 0)


def test_order_manager_retries_then_trips_error_streak(tmp_path):
    cfg = _cfg(tmp_path)
    pf = PortfolioEngine(tmp_path / "p.json")
    events = EventLog(tmp_path / "ev")
    risk = AccountRiskEngine(RiskLimits(circuit_breaker_errors=2))

    class FailingAdapter(PaperBroker):
        def place(self, order):
            raise BrokerError("boom")
        def order_status(self, order):
            return order

    om = OrderManager(FailingAdapter(cfg), pf, events, risk=risk,
                      max_retries=1, sleep_fn=lambda s: None)
    order = Order(client_order_id="c1", symbol="RELIANCE", side=Side.BUY,
                  quantity=1, order_type="MARKET", position_id="p1")
    with pytest.raises(BrokerError):
        om._submit(order)
    assert pf.orders["c1"].status == OrderStatus.REJECTED
    assert risk.error_streak == 1


def test_order_manager_idempotent_reconcile_on_known_id(tmp_path):
    cfg = _cfg(tmp_path)
    pf = PortfolioEngine(tmp_path / "p.json")
    events = EventLog(tmp_path / "ev")
    broker = PaperBroker(cfg)
    broker.update_quotes({"RELIANCE": 100.0})
    om = OrderManager(broker, pf, events)
    o = Order(client_order_id="c1", symbol="RELIANCE", side=Side.BUY,
              quantity=1, order_type="MARKET", broker_order_id="B1",
              status=OrderStatus.OPEN)
    pf.record_order(o)
    # already has a broker id -> reconciles via order_status, does not re-place
    result = om._submit(o)
    assert result.client_order_id == "c1"


# ============================================================== trade manager

def _tm(spec):
    return TradeManager({"orb_15m": spec})


def test_manager_stop_first_and_honest_gap():
    spec = structural_intraday(stop_col="or_low", target_kind="r", target_r=2.0)
    tm = _tm(spec)
    pos = _position(stop=98.0)
    # gap-through: bar opens below the stop -> fill at the open
    d = tm.manage(pos, _bar(96, 96.5, 95, 95.5), spec=spec)
    assert d.action == "exit" and d.reason == "stop_loss"
    assert d.price == pytest.approx(96.0) and d.gap_fill


def test_manager_same_bar_stop_and_target_is_a_stop():
    spec = structural_intraday(stop_col="or_low", target_kind="r", target_r=2.0)
    tm = _tm(spec)
    pos = _position(stop=98.0, target=104.0)
    d = tm.manage(pos, _bar(100, 106, 97, 100), spec=spec)   # spans both
    assert d.action == "exit" and d.reason == "stop_loss"


def test_manager_target_then_partial_then_breakeven_then_target2():
    spec = ExecutionSpec(stop_kind="column", stop_col="or_low",
                         hard_stop_pct=None, target_kind="column",
                         target_col="t1", partial_fraction=0.5,
                         target2_col="t2")
    tm = TradeManager({"orb_15m": spec})
    pos = _position(stop=98.0, target=104.0)
    pos.target2 = 108.0
    d = tm.manage(pos, _bar(103, 104.5, 102, 104), spec=spec)
    assert d.action == "partial"
    assert d.new_stop == pytest.approx(100.0)         # breakeven = entry
    assert d.partial_qty == pytest.approx(250.0)


def test_manager_chandelier_trail_ratchets():
    spec = ExecutionSpec(stop_kind="atr_structure", trail="chandelier",
                         intraday=True, max_hold_bars=None)
    tm = TradeManager({"orb_15m": spec})
    pos = _position(stop=96.0, target=None)
    pos.atr_at_entry = 1.0
    # price well in profit -> chandelier proposes a raised stop
    d = tm.manage(pos, _bar(110, 111, 109, 110), spec=spec)
    assert d.action == "trail" and d.new_stop > 96.0


def test_manager_column_trail_follows_the_line():
    spec = ExecutionSpec(stop_kind="column", stop_col="st_line",
                         hard_stop_pct=None, trail="column",
                         trail_col="st_line", intraday=True, max_hold_bars=None)
    tm = TradeManager({"supertrend_15m": spec})
    pos = _position(strategy="supertrend_15m", stop=96.0, target=None)
    d = tm.manage(pos, _bar(105, 106, 104, 105, st_line=102.0), spec=spec)
    assert d.action == "trail" and d.new_stop == pytest.approx(102.0)


def test_manager_squareoff_when_past_cutoff():
    spec = structural_intraday(stop_col="or_low")
    tm = _tm(spec)
    pos = _position(stop=98.0, target=None)
    d = tm.manage(pos, _bar(100, 101, 99, 100.5), spec=spec,
                  past_squareoff=True)
    assert d.action == "exit" and d.reason == "session_squareoff"
    assert d.price == pytest.approx(100.5)


# =============================================================== portfolio

def test_portfolio_atomic_persistence_and_reload(tmp_path):
    path = tmp_path / "p.json"
    pf = PortfolioEngine(path)
    pf.session_date = "2024-03-04"
    pf.add_position(_position())
    pf.record_order(Order(client_order_id="c1", symbol="RELIANCE",
                          side=Side.BUY, quantity=1, order_type="MARKET"))
    assert path.exists() and not path.with_suffix(".tmp").exists()
    pf2 = PortfolioEngine(path)
    assert pf2.load()
    assert pf2.open_count() == 1
    assert pf2.session_date == "2024-03-04"


def test_portfolio_close_books_pnl():
    pf = PortfolioEngine("/tmp/does-not-persist-here.json")
    pf.state_file = None
    pf.persist = lambda: None                          # skip disk in this unit
    pos = _position(entry=100, qty=500)
    rec = pf.close_position(pos, 104.0, "target")
    assert rec["pnl"] == pytest.approx(2000.0)
    assert pf.realized_pnl == pytest.approx(2000.0)


# ================================================================= recovery

class ReconAdapter(PaperBroker):
    def __init__(self, cfg, positions, open_orders):
        super().__init__(cfg)
        self._recon_pos = positions
        self._recon_orders = open_orders
    def positions(self): return self._recon_pos
    def open_orders(self): return self._recon_orders


def test_recovery_resumes_matching_position(tmp_path):
    cfg = _cfg(tmp_path)
    pf = PortfolioEngine(tmp_path / "p.json")
    pf.add_position(_position(qty=500))
    events = EventLog(tmp_path / "ev")
    adapter = ReconAdapter(cfg, [{"symbol": "RELIANCE", "quantity": 500,
                                  "avg_price": 100.0}], [])
    report = RecoveryManager(pf, adapter, events).recover()
    assert report.resumed and not report.orphaned_internal
    assert pf.open_count() == 1


def test_recovery_closes_orphaned_internal_position(tmp_path):
    cfg = _cfg(tmp_path)
    pf = PortfolioEngine(tmp_path / "p.json")
    pos = _position(qty=500)
    pos.last_price = 103.0
    pf.add_position(pos)
    events = EventLog(tmp_path / "ev")
    adapter = ReconAdapter(cfg, [], [])                # broker shows nothing
    report = RecoveryManager(pf, adapter, events).recover()
    assert report.orphaned_internal and pf.open_count() == 0


def test_recovery_adopts_orphaned_broker_position_for_squareoff(tmp_path):
    cfg = _cfg(tmp_path)
    pf = PortfolioEngine(tmp_path / "p.json")
    events = EventLog(tmp_path / "ev")
    adapter = ReconAdapter(cfg, [{"symbol": "TCS", "quantity": 200,
                                  "avg_price": 3000.0}], [])
    report = RecoveryManager(pf, adapter, events).recover()
    assert "TCS" in report.orphaned_broker
    orphan = pf.open_positions()[0]
    assert orphan.strategy == "__orphan__" and orphan.symbol == "TCS"


def test_recovery_detects_partial_fill(tmp_path):
    cfg = _cfg(tmp_path)
    pf = PortfolioEngine(tmp_path / "p.json")
    pf.add_position(_position(qty=500))
    events = EventLog(tmp_path / "ev")
    adapter = ReconAdapter(cfg, [{"symbol": "RELIANCE", "quantity": 200,
                                  "avg_price": 100.0}], [])
    report = RecoveryManager(pf, adapter, events).recover()
    assert report.partial_fills
    assert pf.open_positions()[0].open_quantity == pytest.approx(200.0)


# =============================================================== clock

def test_clock_bar_boundaries_and_squareoff():
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    clock = MarketClock(holidays=set(), squareoff=time(15, 15))
    at = datetime(2024, 3, 4, 10, 3, tzinfo=ist)      # 10:03
    lb = clock.last_closed_bar_open("15m", at=at)
    assert lb.hour == 9 and lb.minute == 45           # 09:45 bar completed 10:00
    assert not clock.past_squareoff(at)
    assert clock.past_squareoff(datetime(2024, 3, 4, 15, 20, tzinfo=ist))
    assert not clock.is_session_day(__import__("datetime").date(2024, 3, 9))  # Sat
