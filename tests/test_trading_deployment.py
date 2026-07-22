"""Deployment-phase integration: scheduler cadence, live-adapter robustness
(session refresh / rate-limit / reconnect / duplicate prevention), and the
central guarantee that PAPER and LIVE drive the EXACT same pipeline with only
the adapter differing.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from algo.trading.adapters.angelone import AngelOneBroker
from algo.trading.clock import MarketClock
from algo.trading.config import LIVE_ENV_KEY, TradingConfig
from algo.trading.engine import ProductionEngine
from algo.marketdata import MarketState, PollReport, TimeframeScheduler
from algo.trading.models import Order, OrderStatus, Side
from algo.strategies.library import OpeningRangeBreakout

IST = ZoneInfo("Asia/Kolkata")


# ============================================================== scheduler
#
# The bar-cadence guarantees the deployment phase established, now enforced on
# TimeframeScheduler - the ONE scheduler. They were previously checked on a
# separate bar timer that decided cadence independently of fetching, which is
# how a timeframe could be "due" for a scan whose data had not been requested.

class _CurrentState:
    """A watchlist whose stored bars are always the expected bar, so a pass
    completes with nothing to fetch - isolating the DUE logic."""
    def __init__(self, symbols=("AAA",)):
        self.symbols = list(symbols)
        self._health = {}

    def usable_symbols(self, timeframe=None): return list(self.symbols)
    def last_bar(self, symbol, timeframe): return pd.Timestamp.max.tz_localize("UTC")

    def tf_health(self, timeframe):
        from algo.marketdata.state import TimeframeHealth
        return self._health.setdefault(timeframe,
                                       TimeframeHealth(timeframe=timeframe))


def test_scheduler_marks_timeframes_due_once_per_bar():
    clock = MarketClock(holidays=set())
    sched = TimeframeScheduler(clock, ["15m", "1h"], grace_seconds=20)
    state = _CurrentState()
    at = datetime(2024, 3, 4, 10, 30, 30, tzinfo=IST)   # 30s after 10:30
    due = sched.due_timeframes(at=at)
    assert "15m" in due and "1h" in due                 # both bars closed
    sched.plan(state, at=at)                            # serve them
    assert sched.due_timeframes(at=at) == []            # not due again
    # 15m advances at 10:45; 1h not until 11:00
    at2 = datetime(2024, 3, 4, 10, 45, 25, tzinfo=IST)
    assert sched.due_timeframes(at=at2) == ["15m"]


def test_scheduler_respects_grace_window():
    clock = MarketClock(holidays=set())
    sched = TimeframeScheduler(clock, ["15m"], grace_seconds=30)
    at = datetime(2024, 3, 4, 10, 30, 10, tzinfo=IST)   # only 10s past close
    assert sched.due_timeframes(at=at) == []            # inside grace
    assert sched.due_timeframes(
        at=datetime(2024, 3, 4, 10, 30, 40, tzinfo=IST)) == ["15m"]


def test_scheduler_quiet_outside_market_hours():
    clock = MarketClock(holidays=set())
    sched = TimeframeScheduler(clock, ["15m"])
    assert sched.due_timeframes(
        at=datetime(2024, 3, 4, 8, 0, tzinfo=IST)) == []      # pre-open
    assert sched.due_timeframes(
        at=datetime(2024, 3, 9, 10, 30, tzinfo=IST)) == []    # Saturday


# ==================================================== angelone robustness

class FakeClient:
    """A minimal SmartAPI SDK stand-in that can be told to fail N times with a
    given error before succeeding - to exercise retry/refresh/reconnect."""
    def __init__(self):
        self.calls = []
        self.place_error = None
        self.place_error_times = 0
        self.next_order_id = 100

    def placeOrder(self, params):
        self.calls.append(("place", params))
        if self.place_error and self.place_error_times > 0:
            self.place_error_times -= 1
            raise RuntimeError(self.place_error)
        self.next_order_id += 1
        return {"status": True, "data": {"orderid": str(self.next_order_id)}}

    def orderBook(self):
        self.calls.append(("orderBook", None))
        return {"status": True, "data": []}

    def position(self):
        self.calls.append(("position", None))
        return {"status": True, "data": []}


class FakeSession:
    def __init__(self, client):
        self._client = client
        self.logged_in = True
        self.refreshes = 0
        self.logins = 0

    @property
    def client(self):
        return self._client

    def ensure(self):
        return self

    def refresh(self):
        self.refreshes += 1

    def login(self):
        self.logins += 1


class FakeInstruments:
    exchange = "NSE"
    def token_for(self, s): return "3045"
    def tradingsymbol_for(self, s): return f"{s}-EQ"
    def ensure(self): return self


def _live_cfg(tmp_path, monkeypatch, armed=True):
    if armed:
        monkeypatch.setenv(LIVE_ENV_KEY, "YES")
    else:
        monkeypatch.delenv(LIVE_ENV_KEY, raising=False)
    (tmp_path / "syms.txt").write_text("RELIANCE\n")
    return TradingConfig.from_dict({
        "mode": "live", "live_trading_enabled": True,
        "state_dir": str(tmp_path / "s"), "store_dir": str(tmp_path / "d"),
        "symbols_file": str(tmp_path / "syms.txt"),
        "broker_min_interval_s": 0.0, "broker_retry_backoff_s": 0.0,
    })


def _broker(cfg):
    client = FakeClient()
    session = FakeSession(client)
    broker = AngelOneBroker(cfg, session=session, instruments=FakeInstruments(),
                            sleep_fn=lambda s: None)
    return broker, client, session


def _order():
    return Order(client_order_id="CO-abc", symbol="RELIANCE", side=Side.BUY,
                 quantity=10, order_type="MARKET")


def test_live_place_succeeds_when_armed(tmp_path, monkeypatch):
    broker, client, _ = _broker(_live_cfg(tmp_path, monkeypatch))
    order = broker.place(_order())
    assert order.status == OrderStatus.OPEN and order.broker_order_id == "101"


def test_live_retries_and_refreshes_on_auth_expiry(tmp_path, monkeypatch):
    broker, client, session = _broker(_live_cfg(tmp_path, monkeypatch))
    client.place_error = "Invalid Token"           # auth expiry
    client.place_error_times = 1
    order = broker.place(_order())
    assert order.status == OrderStatus.OPEN
    assert session.refreshes == 1                  # refreshed once, then retried


def test_live_backs_off_on_rate_limit(tmp_path, monkeypatch):
    broker, client, _ = _broker(_live_cfg(tmp_path, monkeypatch))
    client.place_error = "exceeding access rate"
    client.place_error_times = 2
    order = broker.place(_order())
    assert order.status == OrderStatus.OPEN
    assert sum(1 for c in client.calls if c[0] == "place") == 3


def test_live_reconnects_on_network_error(tmp_path, monkeypatch):
    broker, client, session = _broker(_live_cfg(tmp_path, monkeypatch))
    client.place_error = "Connection timed out"
    client.place_error_times = 1
    order = broker.place(_order())
    assert order.status == OrderStatus.OPEN
    assert session.logins == 1                      # reconnected once


def test_live_keepalive_refreshes_session(tmp_path, monkeypatch):
    broker, _, session = _broker(_live_cfg(tmp_path, monkeypatch))
    broker.keepalive()
    assert session.refreshes == 1


def test_unarmed_live_still_refuses_orders(tmp_path, monkeypatch):
    broker, _, _ = _broker(_live_cfg(tmp_path, monkeypatch, armed=False))
    from algo.trading.adapters.base import BrokerError
    with pytest.raises(BrokerError, match="not armed"):
        broker.place(_order())


# ==================================== paper == live pipeline equivalence

class CraftedFeed(MarketState):
    """MarketState serving in-memory frames; only ``history`` is substituted."""
    def __init__(self, frames):
        super().__init__(store=None, symbols=list(frames),
                         timeframes=["15m"], history_bars=5000)
        self._frames = frames
        self.set_symbols(list(frames))
    def history(self, s, tf): return self._frames.get(s, pd.DataFrame()).copy()


def _orb_breakout_frame():
    frames = []
    for d in pd.bdate_range("2024-02-20", periods=6):
        t = pd.date_range(f"{d.date()} 03:45", periods=8, freq="15min",
                          tz="UTC")
        frames.append(pd.DataFrame({
            "date": t, "open": [100] * 8, "high": [100.3] * 8,
            "low": [99.6] * 8, "close": [100.1] * 8, "volume": [1000] * 8}))
    t = pd.date_range("2024-02-28 03:45", periods=8, freq="15min", tz="UTC")
    frames.append(pd.DataFrame({
        "date": t,
        "open": [100, 100.2, 100.1, 100.3, 100.2, 100.4, 100.5, 101.4],
        "high": [100.5, 100.6, 100.5, 100.6, 100.5, 100.7, 100.8, 101.8],
        "low": [99.6, 99.8, 99.7, 99.9, 99.8, 100.0, 100.1, 101.0],
        "close": [100, 100.2, 100.1, 100.3, 100.2, 100.4, 100.5, 101.5],
        "volume": [1000, 1000, 1000, 1000, 1000, 1000, 1000, 5000]}))
    return pd.concat(frames, ignore_index=True)


def _engine(tmp_path, mode, monkeypatch, adapter=None):
    (tmp_path / "syms.txt").write_text("RELIANCE\n")
    cfg = TradingConfig.from_dict({
        "mode": mode, "state_dir": str(tmp_path / f"s_{mode}"),
        "store_dir": str(tmp_path / "d"),
        "symbols_file": str(tmp_path / "syms.txt"), "timeframes": ["15m"],
        "entry_cutoff_hour": 23, "squareoff_hour": 23,
        "live_trading_enabled": mode == "live",
        "capital": {"deploy_today": 500000},
        # keep the exporter inside tmp_path: the default is the REAL
        # dashboard/dashboard_data, so a test run would otherwise overwrite the
        # snapshots of an actual trading session
        "dashboard_dir": str(tmp_path / f"dash_{mode}"),
    })
    feed = CraftedFeed({"RELIANCE": _orb_breakout_frame()})
    eng = ProductionEngine(cfg, state=feed, strategies=[OpeningRangeBreakout()],
                           adapter=adapter)
    eng.clock.past_entry_cutoff = lambda at=None: False
    eng.clock.past_squareoff = lambda at=None: False
    return eng


def test_paper_and_live_open_identical_positions_only_adapter_differs(
        tmp_path, monkeypatch):
    # PAPER
    paper = _engine(tmp_path, "paper", monkeypatch)
    assert paper.startup()
    paper.run_cycle("15m")
    ppos = paper.portfolio.open_positions()

    # LIVE (armed, fake SDK). Same engine class, same config except mode.
    monkeypatch.setenv(LIVE_ENV_KEY, "YES")
    client = FakeClient()
    session = FakeSession(client)
    cfg = TradingConfig.from_dict({
        "mode": "live", "live_trading_enabled": True,
        "state_dir": str(tmp_path / "s_live"), "store_dir": str(tmp_path / "d"),
        "symbols_file": str(tmp_path / "syms.txt"), "timeframes": ["15m"],
        "entry_cutoff_hour": 23, "squareoff_hour": 23,
        "broker_min_interval_s": 0.0,
        "capital": {"deploy_today": 500000},
        "dashboard_dir": str(tmp_path / "dash_live")})
    live_adapter = AngelOneBroker(cfg, session=session,
                                  instruments=FakeInstruments(),
                                  sleep_fn=lambda s: None)
    feed = CraftedFeed({"RELIANCE": _orb_breakout_frame()})
    live = ProductionEngine(cfg, state=feed,
                            strategies=[OpeningRangeBreakout()],
                            adapter=live_adapter)
    live.clock.past_entry_cutoff = lambda at=None: False
    live.clock.past_squareoff = lambda at=None: False
    assert live.startup()
    live.run_cycle("15m")
    lpos = live.portfolio.open_positions()

    # SAME pipeline decision: one position, same symbol/strategy/qty/stop.
    assert len(ppos) == len(lpos) == 1
    assert ppos[0].symbol == lpos[0].symbol == "RELIANCE"
    assert ppos[0].strategy == lpos[0].strategy == "orb_15m"
    assert ppos[0].quantity == pytest.approx(lpos[0].quantity)
    assert ppos[0].stop == pytest.approx(lpos[0].stop)
    # the ONLY difference is the adapter that placed the order
    assert paper.adapter.name == "paper" and live.adapter.name == "angelone"
    # live actually routed a real placeOrder through the SDK
    assert any(c[0] == "place" for c in client.calls)


def test_tick_manages_without_scan_when_nothing_due(tmp_path, monkeypatch):
    eng = _engine(tmp_path, "paper", monkeypatch)
    eng.startup()
    # force market data to report nothing due -> tick still runs a mgmt pass
    monkeypatch.setattr(eng.marketdata, "poll", lambda **kwargs: PollReport())
    result = eng.tick()
    assert result["due"] == [] and result["opened"] == 0
