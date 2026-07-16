"""MarketDataStore - efficient local OHLCV storage (parquet).

Candles live in parquet, never in the evidence SQLite (decision D-010): columnar,
compressed, fast to read, cheap to keep. Layout is one file per symbol x
timeframe:

    <root>/<timeframe>/<symbol>.parquet

Writes are UPSERTS keyed on timestamp: existing + new are merged and duplicate
timestamps collapse to the last value. So re-ingesting an overlapping window can
never create duplicate candles - the store is the second line of duplicate
protection (the first is the ingestion engine only fetching the missing window).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

import pandas as pd

from algo.core.logging import get_logger
from algo.data import ohlcv

logger = get_logger("data.store")

_UNSAFE = re.compile(r'[\\/:*?"<>|]')


def _safe(name: str) -> str:
    return _UNSAFE.sub("_", name)


class MarketDataStore:
    """Parquet-backed OHLCV store partitioned by timeframe and symbol."""

    def __init__(self, root) -> None:
        self.root = Path(root)

    # ------------------------------------------------------------- locations

    def _dir(self, timeframe: str) -> Path:
        return self.root / timeframe

    def _path(self, symbol: str, timeframe: str) -> Path:
        return self._dir(timeframe) / f"{_safe(symbol)}.parquet"

    def exists(self, symbol: str, timeframe: str) -> bool:
        return self._path(symbol, timeframe).exists()

    # ----------------------------------------------------------------- write

    def write(self, symbol: str, timeframe: str, frame: pd.DataFrame) -> int:
        """Upsert bars for ``symbol``/``timeframe``. Returns rows newly added.

        Merges with any existing data and collapses duplicate timestamps (last
        wins), so overlapping re-ingestion is safe and idempotent.
        """
        if ohlcv.is_empty(frame):
            return 0
        incoming = ohlcv.normalize(frame)
        path = self._path(symbol, timeframe)
        before = 0
        if path.exists():
            existing = pd.read_parquet(path)
            before = len(existing)
            incoming = ohlcv.normalize(pd.concat([existing, incoming],
                                                 ignore_index=True))
        path.parent.mkdir(parents=True, exist_ok=True)
        incoming.to_parquet(path, index=False)
        added = len(incoming) - before
        logger.info("store.write %s/%s: %d rows (+%d new)",
                    symbol, timeframe, len(incoming), added)
        return added

    # ------------------------------------------------------------------ read

    def read(self, symbol: str, timeframe: str,
             start=None, end=None) -> pd.DataFrame:
        """Read stored bars, optionally clipped to [start, end] (empty if none)."""
        path = self._path(symbol, timeframe)
        if not path.exists():
            return pd.DataFrame(columns=list(ohlcv.OHLCV_COLUMNS))
        frame = pd.read_parquet(path)
        if start is not None:
            frame = frame[frame["date"] >= ohlcv.to_utc(start)]
        if end is not None:
            frame = frame[frame["date"] <= ohlcv.to_utc(end)]
        return frame.reset_index(drop=True)

    def tail(self, symbol: str, timeframe: str, n: int) -> pd.DataFrame:
        """Most recent ``n`` bars (for the scanner's per-symbol window)."""
        frame = self.read(symbol, timeframe)
        return frame.tail(n).reset_index(drop=True)

    # -------------------------------------------------------------- coverage

    def coverage(self, symbol: str, timeframe: str) -> Optional[dict]:
        """{'start', 'end', 'rows'} for stored data, or None if absent/empty."""
        frame = self.read(symbol, timeframe)
        if ohlcv.is_empty(frame):
            return None
        return {"start": frame["date"].iloc[0], "end": frame["date"].iloc[-1],
                "rows": int(len(frame))}

    def last_date(self, symbol: str, timeframe: str) -> Optional[pd.Timestamp]:
        cov = self.coverage(symbol, timeframe)
        return cov["end"] if cov else None

    # ------------------------------------------------------------ discovery

    def timeframes(self) -> List[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def symbols(self, timeframe: str) -> List[str]:
        d = self._dir(timeframe)
        if not d.exists():
            return []
        return sorted(p.stem for p in d.glob("*.parquet"))
