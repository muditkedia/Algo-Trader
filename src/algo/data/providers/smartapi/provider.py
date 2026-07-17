"""SmartApiDataProvider - historical candles + latest quote via SmartAPI.

Implements the platform ``DataProvider`` contract with the SDK's documented
``getCandleData`` API:

    {"exchange": "NSE", "symboltoken": "3045", "interval": "FIFTEEN_MINUTE",
     "fromdate": "2021-02-08 09:00", "todate": "2021-02-08 15:30"}

Candle rows come back as [timestamp, open, high, low, close, volume] with an
IST offset ("2021-02-08T09:15:00+05:30"); they are normalized to the canonical
UTC OHLCV frame. Window dates are sent in IST (exchange local time).

Long ranges are CHUNKED per SmartAPI's documented per-request day limits and
THROTTLED to respect the documented historical rate limit (~3 req/s). Both are
configurable (``max_days_per_request``, ``min_request_interval_s``) - they are
API constraints, not magic numbers. A ``TokenException``-style failure triggers
ONE session refresh + retry.

Latest quote uses the documented ``ltpData(exchange, tradingsymbol, token)``.
"""

from __future__ import annotations

import time as time_mod
from typing import Callable, Dict, List, Optional

import pandas as pd

from algo.core.logging import get_logger
from algo.data.ohlcv import OHLCV_COLUMNS, day_end, normalize, to_utc
from algo.data.providers.base import DataProvider

logger = get_logger("data.smartapi.provider")

IST = "Asia/Kolkata"

#: Platform timeframe -> official SmartAPI interval name.
INTERVALS: Dict[str, str] = {
    "1m": "ONE_MINUTE", "3m": "THREE_MINUTE", "5m": "FIVE_MINUTE",
    "10m": "TEN_MINUTE", "15m": "FIFTEEN_MINUTE", "30m": "THIRTY_MINUTE",
    "1h": "ONE_HOUR", "1d": "ONE_DAY",
}

#: Documented max days per getCandleData request, per interval (configurable).
MAX_DAYS_PER_REQUEST: Dict[str, int] = {
    "1m": 30, "3m": 60, "5m": 100, "10m": 100, "15m": 200, "30m": 200,
    "1h": 400, "1d": 2000,
}


class SmartApiDataError(RuntimeError):
    """Raised when a SmartAPI data request fails after retry."""


def _fmt_ist(ts: pd.Timestamp) -> str:
    return ts.tz_convert(IST).strftime("%Y-%m-%d %H:%M")


class SmartApiDataProvider(DataProvider):
    name = "smartapi"

    def __init__(self, session, instruments, exchange: str = "NSE",
                 max_days_per_request: Optional[Dict[str, int]] = None,
                 min_request_interval_s: float = 0.4,
                 sleep_fn: Optional[Callable] = None) -> None:
        self.session = session
        self.instruments = instruments
        self.exchange = exchange
        self.max_days = {**MAX_DAYS_PER_REQUEST, **(max_days_per_request or {})}
        self.min_interval = min_request_interval_s
        self.sleep = sleep_fn or time_mod.sleep
        self._last_request = 0.0

    def list_symbols(self) -> List[str]:
        return self.instruments.symbols()

    # ------------------------------------------------------------- throttle

    def _throttle(self) -> None:
        elapsed = time_mod.monotonic() - self._last_request
        if elapsed < self.min_interval:
            self.sleep(self.min_interval - elapsed)
        self._last_request = time_mod.monotonic()

    # -------------------------------------------------------------- candles

    def _request_candles(self, params: dict) -> list:
        """One getCandleData call with throttle + one refresh-retry."""
        self._throttle()
        client = self.session.ensure().client
        response = client.getCandleData(params)
        if isinstance(response, dict) and not response.get("status", False):
            message = str(response.get("message") or "")
            error_type = str(response.get("errorcode")
                             or response.get("error_type") or "")
            if "token" in (message + error_type).lower():
                logger.info("session expired mid-download - refreshing once")
                self.session.refresh()
                self._throttle()
                response = client.getCandleData(params)
        if not isinstance(response, dict) or not response.get("status", False):
            detail = (response or {}).get("message", "?") \
                if isinstance(response, dict) else type(response).__name__
            raise SmartApiDataError(f"getCandleData failed: {detail}")
        return response.get("data") or []

    def fetch_ohlcv(self, symbol: str, timeframe: str, start, end) -> pd.DataFrame:
        empty = pd.DataFrame(columns=list(OHLCV_COLUMNS))
        interval = INTERVALS.get(timeframe)
        if interval is None:
            logger.warning("unsupported timeframe %r (known: %s)",
                           timeframe, sorted(INTERVALS))
            return empty
        token = self.instruments.token_for(symbol)
        if token is None:
            logger.warning("no instrument token for %s - skipping", symbol)
            return empty

        start_ts, end_ts = to_utc(start), day_end(end)
        chunk = pd.Timedelta(days=self.max_days.get(timeframe, 30))
        rows: list = []
        window_start = start_ts
        while window_start <= end_ts:
            window_end = min(window_start + chunk - pd.Timedelta(minutes=1),
                             end_ts)
            data = self._request_candles({
                "exchange": self.exchange, "symboltoken": token,
                "interval": interval,
                "fromdate": _fmt_ist(window_start),
                "todate": _fmt_ist(window_end),
            })
            rows.extend(data)
            window_start = window_end + pd.Timedelta(minutes=1)

        if not rows:
            return empty
        frame = pd.DataFrame(rows, columns=["date", "open", "high", "low",
                                            "close", "volume"])
        frame = normalize(frame)                      # IST offsets -> UTC, dedup
        return frame[(frame["date"] >= start_ts)
                     & (frame["date"] <= end_ts)].reset_index(drop=True)

    # ----------------------------------------------------------------- quote

    def latest_quote(self, symbol: str) -> Optional[dict]:
        """Latest traded price via the documented ltpData endpoint."""
        token = self.instruments.token_for(symbol)
        tradingsymbol = self.instruments.tradingsymbol_for(symbol)
        if token is None or tradingsymbol is None:
            logger.warning("no instrument mapping for %s", symbol)
            return None
        self._throttle()
        client = self.session.ensure().client
        response = client.ltpData(self.exchange, tradingsymbol, token)
        if not isinstance(response, dict) or not response.get("status", False):
            logger.warning("ltpData failed for %s: %s", symbol,
                           (response or {}).get("message", "?")
                           if isinstance(response, dict) else response)
            return None
        return response.get("data") or None
