"""KotakNeoDataProvider - DataProvider backed by the Kotak Neo quotes OHLC.

Implements the Phase-2 ``DataProvider`` contract so it plugs straight into the
existing ``IngestionEngine`` / ``MarketDataStore`` / ``DataJobs`` / ``ScanEngine``
with zero changes elsewhere.

SDK REALITY (verified against the supplied official Kotak Neo v2 SDK): there is
NO historical-candle endpoint. The only OHLC source is
``quotes(quote_type='ohlc')``, which returns the CURRENT session's bar. So
``fetch_ohlcv`` produces at most one bar - today's daily bar - when the request
window includes today. Combined with the incremental scheduler this ACCUMULATES
a daily OHLCV history going forward (exactly "incremental updates only"), and the
store's upsert keeps it idempotent (a partial intraday snapshot is overwritten by
the final EOD bar).

For bulk BACKFILL of past history, use the existing ``CsvDataProvider`` to import
a broker/vendor export - or wire Kotak's separate charts/history endpoint here
later if/when its official spec is provided. This provider does not invent one.

Intraday timeframes are not served here (the SDK's only intraday source is the
live websocket, a separate collector); such requests return empty with a warning.
"""

from __future__ import annotations

from typing import Callable, List, Optional

import pandas as pd

from algo.core.logging import get_logger
from algo.data.ohlcv import OHLCV_COLUMNS, day_end, normalize, to_utc
from algo.data.providers.base import DataProvider

logger = get_logger("data.kotak.provider")

DAILY = "1d"


def _first_record(resp):
    if isinstance(resp, dict):
        for key in ("data", "result", "quotes"):
            value = resp.get(key)
            if isinstance(value, list) and value:
                return value[0]
        if any(k in resp for k in ("ohlc", "open", "ltp", "last_price")):
            return resp
    if isinstance(resp, list) and resp:
        return resp[0]
    return None


def _num(record: dict, candidates) -> Optional[float]:
    for cand in candidates:
        if cand in record and record[cand] not in (None, ""):
            try:
                return float(record[cand])
            except (TypeError, ValueError):
                continue
    return None


def _extract_ohlc(resp) -> Optional[dict]:
    record = _first_record(resp)
    if not isinstance(record, dict):
        return None
    src = record["ohlc"] if isinstance(record.get("ohlc"), dict) else record
    o = _num(src, ("open", "o", "op", "openPrice", "open_price"))
    h = _num(src, ("high", "h", "hp", "highPrice", "high_price"))
    lo = _num(src, ("low", "l", "lp", "lowPrice", "low_price"))
    c = _num(src, ("close", "c", "cp", "closePrice", "close_price")) \
        or _num(record, ("ltp", "last_price", "lastPrice"))
    v = _num(record, ("volume", "v", "vol", "tradedVolume", "ttv",
                      "volume_traded")) or 0.0
    if None in (o, h, lo, c):
        return None
    return {"open": o, "high": h, "low": lo, "close": c, "volume": v}


class KotakNeoDataProvider(DataProvider):
    name = "kotak_neo"

    def __init__(self, session, instruments, exchange_segment: str = "nse_cm",
                 calendar=None, now_fn: Optional[Callable] = None) -> None:
        self.session = session
        self.instruments = instruments
        self.exchange_segment = exchange_segment
        self.calendar = calendar
        self.now_fn = now_fn or (lambda: pd.Timestamp.now(tz="UTC"))

    def list_symbols(self) -> List[str]:
        return self.instruments.symbols(self.exchange_segment)

    # ---------------------------------------------------------------- fetch

    def fetch_ohlcv(self, symbol: str, timeframe: str, start, end) -> pd.DataFrame:
        empty = pd.DataFrame(columns=list(OHLCV_COLUMNS))
        if timeframe != DAILY:
            logger.warning("Kotak provider serves only '1d' (got %r); intraday "
                           "needs the websocket collector - returning empty.",
                           timeframe)
            return empty

        token = self.instruments.token_for(symbol, self.exchange_segment)
        if token is None:
            logger.warning("no instrument token for %s (%s) - skipping",
                           symbol, self.exchange_segment)
            return empty

        resp = self.session.client.quotes(
            instrument_tokens=[{"instrument_token": token,
                                "exchange_segment": self.exchange_segment}],
            quote_type="ohlc")
        ohlc = _extract_ohlc(resp)
        if ohlc is None:
            logger.warning("no OHLC in quotes response for %s", symbol)
            return empty

        bar_ts = pd.Timestamp(self._session_date()).tz_localize("UTC")
        if not (to_utc(start) <= bar_ts <= day_end(end)):
            # The only bar Kotak can give (today's) is outside the requested
            # window - e.g. a pure historical backfill. Nothing to return.
            return empty

        frame = pd.DataFrame([{
            "date": bar_ts, "open": ohlc["open"], "high": ohlc["high"],
            "low": ohlc["low"], "close": ohlc["close"], "volume": ohlc["volume"],
        }])
        return normalize(frame)

    # --------------------------------------------------------------- helpers

    def _session_date(self):
        now = self.now_fn()
        day = pd.Timestamp(now).date()
        if self.calendar is None:
            return day
        if self.calendar.is_session(day):
            return day
        prev = self.calendar.previous_session(day)
        return prev if prev is not None else day
