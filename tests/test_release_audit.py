"""Release-audit regressions.

Two release-blocking defects were found by the independent release audit and
fixed; these tests pin the corrected behaviour.

RB-1  The AngelOne adapter trusted the SmartAPI response ENVELOPE. SmartAPI
      reports business failures (RMS rejection, insufficient funds, market
      closed) as HTTP 200 with ``status: False`` - it does not raise. So a
      REJECTED order was recorded as OPEN, a FAILED cancel was recorded as
      CANCELLED (while still live at the broker), a failed order-book/position
      read looked like "flat", and ``data: None`` crashed the cycle with a raw
      AttributeError that escaped BrokerError handling.

RB-2  Position sizing produced FRACTIONAL share quantities. NSE equities trade
      in whole shares: the live adapter truncated with ``int()`` while the
      portfolio recorded the fraction, so paper and live diverged and every
      restart looked like a partial fill during reconciliation.
"""

import pandas as pd
import pytest

from algo.execution import structural_intraday
from algo.trading.adapters.angelone import AngelOneBroker
from algo.trading.adapters.base import BrokerError
from algo.trading.config import LIVE_ENV_KEY, RiskLimits, TradingConfig
from algo.trading.models import Order, OrderStatus, Side
from algo.trading.portfolio import PortfolioEngine
from algo.trading.risk import AccountRiskEngine
from algo.trading.signals import TradingSignal


class _Inst:
    def token_for(self, s): return "3045"
    def tradingsymbol_for(self, s): return f"{s}-EQ"
    def ensure(self): return self


class _Sess:
    logged_in = True
    def __init__(self, client): self.client = client
    def ensure(self): return self
    def refresh(self): pass
    def login(self): pass


def _broker(monkeypatch, client):
    monkeypatch.setenv(LIVE_ENV_KEY, "YES")
    cfg = TradingConfig.from_dict({
        "mode": "live", "live_trading_enabled": True,
        "broker_min_interval_s": 0.0, "broker_retry_backoff_s": 0.0,
        "broker_max_retries": 0})
    return AngelOneBroker(cfg, session=_Sess(client), instruments=_Inst(),
                          sleep_fn=lambda s: None)


def _order(**kw):
    base = dict(client_order_id="c1", symbol="RELIANCE", side=Side.BUY,
                quantity=10, order_type="MARKET")
    base.update(kw)
    return Order(**base)


# ------------------------------------------------------------------ RB-1

@pytest.mark.parametrize("payload", [
    {"status": False, "message": "insufficient funds", "data": None},
    {"status": False, "message": "RMS blocked"},                # no data key
    {"status": False, "errorcode": "AB1004"},                   # code only
])
def test_rejected_order_is_REJECTED_not_open(monkeypatch, payload):
    class C:
        def placeOrder(self, p): return payload
    broker = _broker(monkeypatch, C())
    order = _order()
    with pytest.raises(BrokerError, match="rejected by broker"):
        broker.place(order)
    assert order.status == OrderStatus.REJECTED     # never OPEN
    assert order.broker_order_id is None


def test_success_without_order_id_is_rejected(monkeypatch):
    class C:
        def placeOrder(self, p): return {"status": True, "data": {}}
    broker = _broker(monkeypatch, C())
    order = _order()
    with pytest.raises(BrokerError, match="no order id"):
        broker.place(order)
    assert order.status == OrderStatus.REJECTED


def test_successful_place_still_works(monkeypatch):
    class C:
        def placeOrder(self, p): return {"status": True,
                                         "data": {"orderid": "251219000001"}}
    broker = _broker(monkeypatch, C())
    order = broker.place(_order())
    assert order.status == OrderStatus.OPEN
    assert order.broker_order_id == "251219000001"


def test_failed_cancel_does_not_mark_order_cancelled(monkeypatch):
    class C:
        def cancelOrder(self, oid, variety):
            return {"status": False, "message": "order already executed"}
    broker = _broker(monkeypatch, C())
    order = _order(side=Side.SELL, broker_order_id="B1",
                   status=OrderStatus.OPEN)
    with pytest.raises(BrokerError, match="rejected by broker"):
        broker.cancel(order)
    assert order.status == OrderStatus.OPEN   # still live -> stays visible


def test_failed_modify_raises(monkeypatch):
    class C:
        def modifyOrder(self, p): return {"status": False, "message": "no"}
    broker = _broker(monkeypatch, C())
    with pytest.raises(BrokerError, match="rejected by broker"):
        broker.modify(_order(broker_order_id="B1"), trigger_price=99.0)


def test_modify_and_cancel_require_broker_id(monkeypatch):
    class C:
        def modifyOrder(self, p): return {"status": True, "data": {}}
        def cancelOrder(self, oid, v): return {"status": True, "data": {}}
    broker = _broker(monkeypatch, C())
    with pytest.raises(BrokerError, match="acknowledged"):
        broker.modify(_order(), trigger_price=1.0)
    with pytest.raises(BrokerError, match="acknowledged"):
        broker.cancel(_order())


def test_failed_reads_raise_instead_of_looking_flat(monkeypatch):
    """A failed orderBook/position read must NOT be mistaken for 'no orders /
    no positions' - recovery would abandon live risk."""
    class C:
        def orderBook(self): return {"status": False, "message": "server busy"}
        def position(self): return {"status": False, "message": "server busy"}
    broker = _broker(monkeypatch, C())
    with pytest.raises(BrokerError):
        broker.open_orders()
    with pytest.raises(BrokerError):
        broker.positions()


def test_bare_string_orderid_response_is_accepted(monkeypatch):
    """Some SDK versions return a bare order-id string on success."""
    class C:
        def placeOrder(self, p): return "251219000009"
    broker = _broker(monkeypatch, C())
    order = broker.place(_order())
    assert order.status == OrderStatus.OPEN
    assert order.broker_order_id == "251219000009"


# ------------------------------------------------------------------ RB-2

def _signal(entry):
    return TradingSignal(
        symbol="RELIANCE", strategy="orb_15m", timeframe="15m",
        bar_time=pd.Timestamp("2024-03-04", tz="UTC"), entry_ref=entry,
        spec=structural_intraday(stop_col="or_low"), stop=entry * 0.99)


def test_position_size_is_whole_shares():
    # deploy 100k -> max/trade 50k; 50000/2450.75 = 20.40 -> 20 whole shares
    risk = AccountRiskEngine(RiskLimits(deploy_today=100_000))
    qty = risk.position_size(_signal(2450.75))
    assert qty == 20.0 and float(qty).is_integer()


def test_expensive_share_above_stake_is_rejected(tmp_path):
    risk = AccountRiskEngine(RiskLimits(deploy_today=100_000))
    pf = PortfolioEngine(tmp_path / "p.json")
    decision = risk.check_entry(_signal(60_000.0), pf)   # 0 whole shares
    assert not decision and "whole lot" in decision.reason


def test_whole_share_quantity_survives_the_int_cast_to_the_broker():
    """The live adapter casts to int(); with whole-share sizing the quantity
    the broker receives equals the quantity the portfolio records."""
    risk = AccountRiskEngine(RiskLimits(deploy_today=100_000))
    qty = risk.position_size(_signal(2450.75))
    assert int(qty) == qty                      # no truncation loss
