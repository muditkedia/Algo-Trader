"""CsvDataProvider - import OHLCV from local CSV files.

The practical offline ingestion path: NSE bhavcopy exports, broker CSV dumps,
and vendor files all arrive as CSV. Layout is ``<root>/<timeframe>/<symbol>.csv``
with columns date/open/high/low/close/volume (case-insensitive; common aliases
accepted). A missing file yields an empty frame - the provider reports only what
exists, it never fabricates.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pandas as pd

from algo.data.ohlcv import OHLCV_COLUMNS, day_end, normalize, to_utc
from algo.data.providers.base import DataProvider

_ALIASES = {
    "timestamp": "date", "datetime": "date", "time": "date",
    "o": "open", "h": "high", "l": "low", "c": "close",
    "vol": "volume", "v": "volume", "qty": "volume",
}


class CsvDataProvider(DataProvider):
    name = "csv"

    def __init__(self, root) -> None:
        self.root = Path(root)

    def _path(self, symbol: str, timeframe: str) -> Path:
        return self.root / timeframe / f"{symbol}.csv"

    def list_symbols(self) -> List[str]:
        if not self.root.exists():
            return []
        stems = set()
        for tf_dir in self.root.iterdir():
            if tf_dir.is_dir():
                stems.update(p.stem for p in tf_dir.glob("*.csv"))
        return sorted(stems)

    def fetch_ohlcv(self, symbol: str, timeframe: str, start, end) -> pd.DataFrame:
        path = self._path(symbol, timeframe)
        if not path.exists():
            return pd.DataFrame(columns=list(OHLCV_COLUMNS))
        raw = pd.read_csv(path)
        raw.columns = [str(c).strip().lower() for c in raw.columns]
        raw = raw.rename(columns={k: v for k, v in _ALIASES.items()
                                  if k in raw.columns})
        frame = normalize(raw)
        start = to_utc(start)
        end = day_end(end)
        return frame[(frame["date"] >= start) & (frame["date"] <= end)]\
            .reset_index(drop=True)
