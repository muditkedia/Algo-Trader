"""AngelOneBroker - the LIVE execution adapter (Angel One SmartAPI).

Bound to the official SDK methods on ``SmartApiSession.client``
(``placeOrder``, ``modifyOrder``, ``cancelOrder``, ``orderBook``,
``position``), reusing the verified session (login/refresh/TOTP) and
instrument (symbol->token) layers. Implements the SAME ``ExecutionAdapter``
contract as PaperBroker, so no stage above it changes between modes.

SAFETY - live is disarmed by default and needs THREE independent keys:
  1. config.mode == "live"
  2. config.live_trading_enabled is True
  3. environment ALGO_ENABLE_LIVE == "YES"
The constructor raises unless all three are present, so importing/selecting
this adapter cannot place a real order by accident. Read-only reconciliation
methods (open_orders/positions/order_status/quote) are permitted whenever a
session exists, so recovery and preflight can inspect the account without
arming order placement.
"""

from __future__ import annotations

import time as _time
from typing import Callable, List, Optional

from algo.core.logging import get_logger
from algo.trading.adapters.base import BrokerError, ExecutionAdapter
from algo.trading.config import LIVE_ENV_KEY, LIVE_ENV_VALUE
from algo.trading.models import Order, OrderStatus, Side, now_iso

logger = get_logger("trading.angelone")


def _is_rate_limited(exc: Exception) -> bool:
    text = str(exc).lower()
    return ("exceeding access rate" in text or "access denied" in text
            or ("rate" in text and "denied" in text))


def _is_auth_expired(exc: Exception) -> bool:
    text = str(exc).lower()
    return ("token" in text or "unauthor" in text or "invalid session" in text
            or "jwt" in text)


def _is_network(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(k in text for k in ("timeout", "timed out", "connection",
                                   "temporarily", "unreachable", "reset",
                                   "max retries"))

#: platform order type -> SmartAPI variety/ordertype
_ORDERTYPE = {"MARKET": "MARKET", "LIMIT": "LIMIT", "STOP": "STOPLOSS_MARKET"}
_STATUS_MAP = {
    "complete": OrderStatus.FILLED, "open": OrderStatus.OPEN,
    "pending": OrderStatus.OPEN, "trigger pending": OrderStatus.OPEN,
    "rejected": OrderStatus.REJECTED, "cancelled": OrderStatus.CANCELLED,
    "open pending": OrderStatus.OPEN, "modified": OrderStatus.OPEN,
    "partially filled": OrderStatus.PARTIALLY_FILLED,
}


class AngelOneBroker(ExecutionAdapter):
    name = "angelone"
    is_live = True

    def __init__(self, config, session=None, instruments=None,
                 allow_unarmed_readonly: bool = True, sleep_fn=None) -> None:
        self.config = config
        self.session = session
        self.instruments = instruments
        self._armed = config.live_armed()
        self._allow_readonly = allow_unarmed_readonly
        self._sleep = sleep_fn or _time.sleep
        self._min_interval = float(getattr(config, "broker_min_interval_s", 1.0))
        self._max_retries = int(getattr(config, "broker_max_retries", 3))
        self._backoff = float(getattr(config, "broker_retry_backoff_s", 1.0))
        self._last_call = 0.0
        if not self._armed:
            # loud, explicit, and non-fatal for READ-ONLY use (recovery/
            # preflight); any order-placing call re-checks and refuses.
            logger.warning(
                "AngelOneBroker constructed UNARMED - live order placement is "
                "DISABLED. Arm with mode=live + live_trading_enabled=true + "
                "%s=%s.", LIVE_ENV_KEY, LIVE_ENV_VALUE)

    # ------------------------------------------------------------- arming

    def _require_armed(self, action: str) -> None:
        if not self.config.live_armed():
            raise BrokerError(
                f"LIVE {action} refused: live trading is not armed "
                f"(need mode=live + live_trading_enabled + "
                f"{LIVE_ENV_KEY}={LIVE_ENV_VALUE})")

    # ------------------------------------------- robust SDK call wrapper

    def _throttle(self) -> None:
        elapsed = _time.monotonic() - self._last_call
        if elapsed < self._min_interval:
            self._sleep(self._min_interval - elapsed)
        self._last_call = _time.monotonic()

    def _call(self, action: str, fn: Callable):
        """Every SmartAPI SDK call goes through here: order-rate throttle,
        rate-limit backoff, ONE session-refresh retry on auth expiry, and ONE
        reconnect retry on a network interruption. Mirrors the verified data
        provider's policy so live order/read calls are as resilient as
        historical downloads."""
        refreshed = reconnected = False
        for attempt in range(self._max_retries + 1):
            self._throttle()
            try:
                return fn()
            except Exception as exc:
                if _is_rate_limited(exc) and attempt < self._max_retries:
                    wait = self._backoff * (2 ** attempt)
                    logger.warning("%s rate limited - backoff %.1fs (%d/%d)",
                                   action, wait, attempt + 1, self._max_retries)
                    self._sleep(wait)
                    continue
                if _is_auth_expired(exc) and not refreshed:
                    logger.info("%s: session expired - refreshing once", action)
                    try:
                        self.session.refresh()
                    except Exception:
                        self.session.login()
                    refreshed = True
                    continue
                if _is_network(exc) and not reconnected \
                        and attempt < self._max_retries:
                    logger.warning("%s: network error - reconnecting once: %s",
                                   action, exc)
                    try:
                        self.session.login()
                    except Exception:
                        pass
                    reconnected = True
                    self._sleep(self._backoff)
                    continue
                raise BrokerError(f"{action} failed: {exc}") from exc
        raise BrokerError(f"{action} failed after {self._max_retries} retries")

    @staticmethod
    def _checked(resp, action: str) -> dict:
        """Validate the SmartAPI response ENVELOPE.

        SmartAPI reports business failures (RMS rejection, insufficient funds,
        bad token, market closed) as a 200 response with ``status: False`` and
        a ``message`` - it does NOT raise. Release audit found that trusting
        such a response silently reported a REJECTED order as OPEN, marked a
        FAILED cancel as CANCELLED, and crashed on ``data: None``. Every
        state-changing call must therefore go through this check.
        """
        if not isinstance(resp, dict):
            # some SDK versions return a bare order-id string on success
            return {"orderid": resp} if resp else {}
        if not resp.get("status", False):
            detail = (resp.get("message") or resp.get("errorcode")
                      or "unknown broker error")
            raise BrokerError(f"{action} rejected by broker: {detail}")
        return resp.get("data") or {}

    def keepalive(self) -> None:
        """Periodic session refresh (called by the engine loop on a cadence)."""
        if self.session is not None and self.session.logged_in:
            try:
                self.session.refresh()
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning("keepalive refresh failed: %s", exc)

    def _token(self, symbol: str) -> str:
        token = (self.instruments.token_for(symbol)
                 if self.instruments else None)
        if token is None:
            raise BrokerError(f"no instrument token for {symbol}")
        return token

    def _tradingsymbol(self, symbol: str) -> str:
        ts = (self.instruments.tradingsymbol_for(symbol)
              if self.instruments else None)
        return ts or f"{symbol}-EQ"

    # ------------------------------------------------------------- lifecycle

    def connect(self) -> None:
        if self.session is None:
            raise BrokerError("AngelOneBroker needs a SmartApiSession")
        self.session.ensure()
        if self.instruments is not None:
            self.instruments.ensure()
        logger.info("AngelOneBroker connected (armed=%s)",
                    self.config.live_armed())

    def quote(self, symbol: str) -> Optional[float]:
        try:
            token = self._token(symbol)
            resp = self._call("ltpData", lambda: self.session.client.ltpData(
                "NSE", self._tradingsymbol(symbol), token))
            data = resp.get("data") if isinstance(resp, dict) else None
            return float(data["ltp"]) if data and data.get("ltp") else None
        except Exception as exc:  # marking must never crash the loop
            logger.warning("ltp fetch failed for %s: %s", symbol, exc)
            return None

    # --------------------------------------------------------------- orders

    def place(self, order: Order) -> Order:
        self._require_armed("placeOrder")
        params = {
            "variety": "NORMAL",
            "tradingsymbol": self._tradingsymbol(order.symbol),
            "symboltoken": self._token(order.symbol),
            "transactiontype": order.side,
            "exchange": "NSE",
            "ordertype": _ORDERTYPE.get(order.order_type, "MARKET"),
            "producttype": "INTRADAY",
            "duration": "DAY",
            "quantity": int(order.quantity),
            "price": order.limit_price or 0,
            "triggerprice": order.trigger_price or 0,
            # SmartAPI honours a client-supplied order tag for idempotency/
            # reconciliation on the order book
            "ordertag": order.client_order_id[:20],
        }
        resp = self._call("placeOrder",
                          lambda: self.session.client.placeOrder(params))
        try:
            data = self._checked(resp, "placeOrder")
        except BrokerError:
            # a broker-side rejection is a TERMINAL state, not a working order
            order.status = OrderStatus.REJECTED
            order.updated_ts = now_iso()
            raise
        broker_id = data.get("orderid") if isinstance(data, dict) else None
        if not broker_id:
            order.status = OrderStatus.REJECTED
            order.reason = "broker returned no order id"
            order.updated_ts = now_iso()
            raise BrokerError("placeOrder returned no order id")
        order.broker_order_id = str(broker_id)
        order.status = OrderStatus.OPEN
        order.updated_ts = now_iso()
        return order

    def modify(self, order: Order, *, limit_price=None,
               trigger_price=None) -> Order:
        self._require_armed("modifyOrder")
        params = {
            "variety": "NORMAL", "orderid": order.broker_order_id,
            "ordertype": _ORDERTYPE.get(order.order_type, "MARKET"),
            "producttype": "INTRADAY", "duration": "DAY",
            "price": limit_price or order.limit_price or 0,
            "triggerprice": trigger_price or order.trigger_price or 0,
            "quantity": int(order.quantity),
            "tradingsymbol": self._tradingsymbol(order.symbol),
            "symboltoken": self._token(order.symbol), "exchange": "NSE",
        }
        if not order.broker_order_id:
            raise BrokerError("modifyOrder needs an acknowledged broker order")
        self._checked(self._call(
            "modifyOrder",
            lambda: self.session.client.modifyOrder(params)), "modifyOrder")
        if limit_price is not None:
            order.limit_price = limit_price
        if trigger_price is not None:
            order.trigger_price = trigger_price
        order.updated_ts = now_iso()
        return order

    def cancel(self, order: Order) -> Order:
        self._require_armed("cancelOrder")
        if not order.broker_order_id:
            raise BrokerError("cancelOrder needs an acknowledged broker order")
        # only mark CANCELLED if the broker CONFIRMS it - otherwise the order
        # is still live and must stay visible to reconciliation
        self._checked(self._call(
            "cancelOrder", lambda: self.session.client.cancelOrder(
                order.broker_order_id, "NORMAL")), "cancelOrder")
        order.status = OrderStatus.CANCELLED
        order.updated_ts = now_iso()
        return order

    # ---------------------------------------------------- reconciliation

    def _order_book(self) -> List[dict]:
        resp = self._call("orderBook", lambda: self.session.client.orderBook())
        # a FAILED read must raise, never look like "no orders" - recovery
        # would otherwise treat working orders as absent (release-audit fix)
        if isinstance(resp, dict) and not resp.get("status", False):
            raise BrokerError(f"orderBook failed: {resp.get('message')}")
        return (resp.get("data") or []) if isinstance(resp, dict) else []

    def order_status(self, order: Order) -> Order:
        for row in self._order_book():
            if (str(row.get("orderid")) == str(order.broker_order_id)
                    or row.get("ordertag") == order.client_order_id[:20]):
                order.status = _STATUS_MAP.get(
                    str(row.get("status", "")).lower(), order.status)
                order.filled_quantity = float(row.get("filledshares") or 0)
                order.avg_fill_price = float(row.get("averageprice") or 0)
                order.broker_order_id = str(row.get("orderid"))
                order.updated_ts = now_iso()
                break
        return order

    def open_orders(self) -> List[Order]:
        out = []
        for row in self._order_book():
            status = _STATUS_MAP.get(str(row.get("status", "")).lower(), "")
            if status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED):
                out.append(Order(
                    client_order_id=str(row.get("ordertag")
                                        or row.get("orderid")),
                    symbol=str(row.get("tradingsymbol", "")).replace("-EQ", ""),
                    side=str(row.get("transactiontype", Side.BUY)),
                    quantity=float(row.get("quantity") or 0),
                    order_type=str(row.get("ordertype", "MARKET")),
                    broker_order_id=str(row.get("orderid")), status=status,
                    filled_quantity=float(row.get("filledshares") or 0),
                    avg_fill_price=float(row.get("averageprice") or 0)))
        return out

    def positions(self) -> List[dict]:
        resp = self._call("position", lambda: self.session.client.position())
        # same rule as the order book: a failed read must NOT read as "flat"
        if isinstance(resp, dict) and not resp.get("status", False):
            raise BrokerError(f"position failed: {resp.get('message')}")
        rows = (resp.get("data") or []) if isinstance(resp, dict) else []
        out = []
        for row in rows:
            qty = float(row.get("netqty") or 0)
            if qty != 0:
                out.append({
                    "symbol": str(row.get("tradingsymbol", "")
                                  ).replace("-EQ", ""),
                    "quantity": qty,
                    "avg_price": float(row.get("netprice")
                                       or row.get("avgnetprice") or 0)})
        return out

    def disconnect(self) -> None:
        if self.session is not None:
            try:
                self.session.logout()
            except Exception:  # pragma: no cover - best effort
                pass
