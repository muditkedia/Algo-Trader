"""Canonical OHLCV contract shared by providers, the store, and quality gates.

One definition of what a bar frame looks like, so every layer agrees:

    date    tz-aware UTC timestamp (bar open/label time)
    open, high, low, close   float
    volume                   float (>= 0)

``normalize`` coerces any provider's output into this shape: UTC timestamps,
sorted ascending, duplicate timestamps collapsed (last wins). This is the OHLCV
analogue of the validation package's ``loaders.ensure_trades`` for trades.
"""

from __future__ import annotations

import pandas as pd

OHLCV_COLUMNS = ("date", "open", "high", "low", "close", "volume")
PRICE_COLUMNS = ("open", "high", "low", "close")

#: Minutes per supported timeframe string.
TIMEFRAME_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "1d": 1440,
}


def timeframe_minutes(timeframe: str) -> int:
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(f"unsupported timeframe {timeframe!r}; "
                         f"known: {sorted(TIMEFRAME_MINUTES)}")
    return TIMEFRAME_MINUTES[timeframe]


def to_utc(ts) -> pd.Timestamp:
    """Coerce any timestamp-like to a UTC tz-aware Timestamp."""
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def day_end(ts) -> pd.Timestamp:
    """Interpret a window end inclusively per calendar day.

    A bare date (midnight) as ``end`` means "the whole of that day", so intraday
    bars up to 23:59 are included - not just the 00:00 instant. Non-midnight
    ends pass through unchanged. Daily bars (labelled 00:00) remain <= end
    either way, so this is correct for every timeframe.
    """
    ts = to_utc(ts)
    if ts.hour == 0 and ts.minute == 0 and ts.second == 0 and ts.microsecond == 0:
        return ts + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    return ts


def normalize(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a canonical OHLCV frame (copy): required columns, UTC-sorted,
    duplicate timestamps collapsed keeping the last. Raises on missing columns.
    """
    missing = [c for c in OHLCV_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"OHLCV frame missing columns: {missing}")
    out = frame.loc[:, list(OHLCV_COLUMNS)].copy()
    out["date"] = pd.to_datetime(out["date"], utc=True)
    for col in PRICE_COLUMNS + ("volume",):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = (out.sort_values("date")
              .drop_duplicates(subset="date", keep="last")
              .reset_index(drop=True))
    return out


def is_empty(frame: pd.DataFrame) -> bool:
    return frame is None or len(frame) == 0
