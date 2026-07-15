"""SyntheticDataProvider - deterministic, session-aware OHLCV for tests/demos.

Generates reproducible bars (seeded per symbol) so the entire ingestion ->
storage -> universe -> scanner pipeline can be built and validated end-to-end
with NO network, credentials, or real feed. It is session-aware: daily bars land
on trading sessions (from a supplied calendar, else weekdays); intraday bars land
inside session hours. This is not market data - it is a stand-in that exercises
the plumbing, exactly as the crypto phase used synthetic frames for its harness.
"""

from __future__ import annotations

import zlib
from datetime import datetime, time
from typing import List, Optional

import numpy as np
import pandas as pd

from algo.data.ohlcv import (
    OHLCV_COLUMNS, day_end, normalize, timeframe_minutes, to_utc,
)
from algo.data.providers.base import DataProvider


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


class SyntheticDataProvider(DataProvider):
    name = "synthetic"

    def __init__(self, seed: int = 7, calendar=None,
                 session_open: str = "09:15", session_close: str = "15:30",
                 start_price: float = 100.0, bar_vol: float = 0.004,
                 symbols: Optional[List[str]] = None) -> None:
        self.seed = seed
        self.calendar = calendar
        self.session_open = _parse_hhmm(session_open)
        self.session_close = _parse_hhmm(session_close)
        self.start_price = start_price
        self.bar_vol = bar_vol
        self._symbols = list(symbols or [])

    def list_symbols(self) -> List[str]:
        return list(self._symbols)

    # ------------------------------------------------------------- internals

    def _session_dates(self, start: pd.Timestamp, end: pd.Timestamp) -> List:
        if self.calendar is not None:
            return self.calendar.sessions(start.date(), end.date())
        days = pd.date_range(start.normalize(), end.normalize(), freq="D")
        return [d.date() for d in days if d.weekday() < 5]  # Mon-Fri

    def _timestamps(self, timeframe: str, sessions: List) -> List[pd.Timestamp]:
        step = timeframe_minutes(timeframe)
        stamps: List[pd.Timestamp] = []
        if step >= timeframe_minutes("1d"):
            for d in sessions:
                stamps.append(pd.Timestamp(d).tz_localize("UTC"))
            return stamps
        open_min = self.session_open.hour * 60 + self.session_open.minute
        close_min = self.session_close.hour * 60 + self.session_close.minute
        for d in sessions:
            base = pd.Timestamp(datetime.combine(d, time(0, 0))).tz_localize("UTC")
            minute = open_min
            while minute < close_min:
                stamps.append(base + pd.Timedelta(minutes=minute))
                minute += step
        return stamps

    def _seed_for(self, symbol: str) -> int:
        return (self.seed * 1_000_003 + zlib.crc32(symbol.encode())) % (2**32)

    # ------------------------------------------------------------- interface

    def fetch_ohlcv(self, symbol: str, timeframe: str, start, end) -> pd.DataFrame:
        start = to_utc(start)
        end = day_end(end)

        sessions = self._session_dates(start, end)
        stamps = self._timestamps(timeframe, sessions)
        if not stamps:
            return pd.DataFrame(columns=list(OHLCV_COLUMNS))

        rng = np.random.default_rng(self._seed_for(symbol))
        n = len(stamps)
        returns = rng.normal(0.0002, self.bar_vol, n)
        close = self.start_price * np.exp(np.cumsum(returns))
        open_ = np.concatenate(([self.start_price], close[:-1]))
        wick = np.abs(rng.normal(0, self.bar_vol / 2, n))
        high = np.maximum(open_, close) * (1 + wick)
        low = np.minimum(open_, close) * (1 - wick)
        volume = rng.lognormal(11.0, 0.4, n).round()

        frame = pd.DataFrame({
            "date": pd.DatetimeIndex(stamps),
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume,
        })
        frame = frame[(frame["date"] >= start) & (frame["date"] <= end)]
        return normalize(frame)
