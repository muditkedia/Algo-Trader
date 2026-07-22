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
For live Market Data v2, pacing and rate-limit retries are owned by
``algo.marketdata.Transport`` so the scheduler can requeue, reprioritize and
report provider pressure without hidden sleeps inside this provider.
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
                 min_request_interval_s: float = 1.0,
                 rate_limit_retries: int = 4,
                 rate_limit_backoff_s: float = 2.0,
                 sleep_fn: Optional[Callable] = None,
                 transport_managed_pacing: bool = False) -> None:
        self.session = session
        self.instruments = instruments
        self.exchange = exchange
        self.max_days = {**MAX_DAYS_PER_REQUEST, **(max_days_per_request or {})}
        # Observed against the live API: the documented "3 req/s" is enforced
        # harder in practice (bulk downloads got "Access denied because of
        # exceeding access rate" at 2.5 req/s), so the default is conservative
        # and rate-limit rejections are retried with exponential backoff rather
        # than being mistaken for bad data.
        self.transport_managed_pacing = bool(transport_managed_pacing)
        self.min_interval = 0.0 if self.transport_managed_pacing \
            else min_request_interval_s
        self.rate_limit_retries = 0 if self.transport_managed_pacing \
            else rate_limit_retries
        self.rate_limit_backoff_s = rate_limit_backoff_s
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

    @staticmethod
    def _is_rate_limited(error: Exception) -> bool:
        """The API reports rate limiting as a non-JSON body, which the SDK
        surfaces as a parse error - so match on the text, not a status code."""
        text = str(error).lower()
        return "exceeding access rate" in text or "access denied" in text \
            or "rate" in text and "denied" in text

    def _call_candles(self, client, params: dict):
        self._throttle()
        return client.getCandleData(params)

    def _request_candles(self, params: dict) -> list:
        """One getCandleData call: throttled, rate-limit backoff, refresh-retry."""
        client = self.session.ensure().client
        response = None
        for attempt in range(self.rate_limit_retries + 1):
            try:
                response = self._call_candles(client, params)
                break
            except Exception as exc:
                if self._is_rate_limited(exc) and attempt < self.rate_limit_retries:
                    wait = self.rate_limit_backoff_s * (2 ** attempt)
                    logger.warning("rate limited - backing off %.1fs "
                                   "(attempt %d/%d)", wait, attempt + 1,
                                   self.rate_limit_retries)
                    self.sleep(wait)
                    continue
                raise

        if isinstance(response, dict) and not response.get("status", False):
            message = str(response.get("message") or "")
            error_type = str(response.get("errorcode")
                             or response.get("error_type") or "")
            if "token" in (message + error_type).lower():
                logger.info("session expired mid-download - refreshing once")
                self.session.refresh()
                response = self._call_candles(client, params)
        if not isinstance(response, dict) or not response.get("status", False):
            detail = (response or {}).get("message", "?") \
                if isinstance(response, dict) else type(response).__name__
            raise SmartApiDataError(f"getCandleData failed: {detail}")
        return response.get("data") or []

    def unavailable_reason(self, symbol: str,
                           timeframe: str) -> Optional[str]:
        """Why this request CANNOT be served at all, or None if it is
        well-formed.

        Separates "unable to fetch" from "nothing new to fetch". An empty frame
        alone cannot tell those apart, and conflating them is what let an
        unresolvable watchlist look exactly like a quiet market (D-038): the
        engine reported ``rows_fetched=0`` all session and traded on stale
        stored candles without one error being raised.
        """
        if INTERVALS.get(timeframe) is None:
            return (f"unsupported timeframe {timeframe!r} "
                    f"(known: {', '.join(sorted(INTERVALS))})")
        if self.instruments.token_for(symbol) is None:
            return "no instrument token in the NSE master"
        return None

    def fetch_ohlcv(self, symbol: str, timeframe: str, start, end) -> pd.DataFrame:
        empty = pd.DataFrame(columns=list(OHLCV_COLUMNS))
        interval = INTERVALS.get(timeframe)
        if interval is None:
            logger.warning("unsupported timeframe %r (known: %s)",
                           timeframe, sorted(INTERVALS))
            return empty
        token = self.instruments.token_for(symbol)
        if token is None:
            # per-symbol detail only: the operator-facing message is the ONE
            # per-cycle mapping summary (IngestionReport.diagnosis), not 99
            # identical warnings that bury everything else in the log.
            logger.debug("no instrument token for %s - skipping", symbol)
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
            logger.debug("no instrument mapping for %s", symbol)
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

    #: Symbols one ``getMarketData`` call will serve. Angel One publishes this
    #: endpoint as a 50-symbol bulk fetch; tokens beyond the cap come back in
    #: the response's ``unfetched`` array rather than raising, so exceeding it
    #: would silently drop symbols instead of failing loudly.
    QUOTE_BATCH = 50

    def fetch_quotes(self, symbols: List[str]) -> Dict[str, float]:
        """Last traded price for MANY symbols in ONE request.

        ``getMarketData(mode, {exchange: [token, ...]})`` is the only bulk
        route this provider has - historical candles have no array form at all
        (verified against smartapi-python 1.5.5: ``getCandleData`` takes a
        single ``symboltoken``). Marking 50 positions therefore costs one
        request here versus 50 through ``latest_quote``, which is the whole
        reason the quote channel can serve a large watchlist while candles stay
        bounded by their own per-symbol budget.

        Caller batches to ``QUOTE_BATCH``; this asks for exactly what it was
        given. Symbols the API could not resolve are simply absent from the
        result - a missing mark leaves the last known price standing, and is
        never escalated into a data fault, because quotes are display-only.
        """
        tokens = {}
        for symbol in symbols:
            token = self.instruments.token_for(symbol)
            if token is not None:
                tokens[str(token)] = symbol
        if not tokens:
            return {}
        self._throttle()
        client = self.session.ensure().client
        response = client.getMarketData("LTP", {self.exchange: list(tokens)})
        if not isinstance(response, dict) or not response.get("status", False):
            logger.warning("getMarketData failed: %s",
                           (response or {}).get("message", "?")
                           if isinstance(response, dict) else response)
            return {}
        data = response.get("data") or {}
        out: Dict[str, float] = {}
        for row in data.get("fetched") or []:
            symbol = tokens.get(str(row.get("symbolToken")))
            try:
                price = float(row.get("ltp"))
            except (TypeError, ValueError):
                continue
            if symbol and price > 0:
                out[symbol] = price
        unfetched = data.get("unfetched") or []
        if unfetched:
            logger.debug("getMarketData returned %d unfetched token(s)",
                         len(unfetched))
        return out
