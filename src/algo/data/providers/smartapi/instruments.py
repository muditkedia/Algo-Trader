"""SmartApiInstruments - the official Angel One instrument master.

SmartAPI publishes one JSON scrip master for all exchanges (documented URL,
configurable via SMARTAPI_INSTRUMENTS_URL). Records look like:

    {"token": "3045", "symbol": "SBIN-EQ", "name": "SBIN",
     "expiry": "", "strike": "-1.0", "lotsize": "1",
     "instrumenttype": "", "exch_seg": "NSE", "tick_size": "5.0"}

This class downloads it (stdlib urllib; downloader injectable for tests),
filters to NSE cash equities (``exch_seg == "NSE"`` and the ``-EQ`` series),
normalizes to a small canonical frame, caches to parquet, and provides the
symbol -> (token, tradingsymbol) lookups the candle/quote APIs need. Symbols
are exposed WITHOUT the ``-EQ`` suffix (RELIANCE, not RELIANCE-EQ) so the rest
of the platform stays broker-agnostic; the suffixed tradingsymbol is kept for
API calls. No auth is required for the master - it works credential-free.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, List, Optional

import pandas as pd

from algo.core.logging import get_logger

logger = get_logger("data.smartapi.instruments")

COLUMNS = ("symbol", "token", "tradingsymbol", "name", "exchange", "lotsize")
CACHE_FILE = "smartapi_nse_eq.parquet"


def _default_downloader(url: str) -> str:  # pragma: no cover - network
    import urllib.request
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read().decode("utf-8")


class SmartApiInstruments:
    def __init__(self, instruments_url: str, cache_dir=None,
                 downloader: Optional[Callable] = None,
                 evidence_logger=None) -> None:
        self.url = instruments_url
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.downloader = downloader or _default_downloader
        self.evidence_logger = evidence_logger
        self._master: Optional[pd.DataFrame] = None

    # ------------------------------------------------------------------ fetch

    def fetch(self, exchange: str = "NSE", series_suffix: str = "-EQ"
              ) -> pd.DataFrame:
        """Download + normalize the master, filtered to cash equities."""
        records = json.loads(self.downloader(self.url))
        frame = pd.DataFrame(records)
        frame = frame[(frame["exch_seg"] == exchange)
                      & frame["symbol"].astype(str).str.endswith(series_suffix)]
        out = pd.DataFrame({
            "symbol": frame["symbol"].astype(str)
            .str.removesuffix(series_suffix).str.strip(),
            "token": frame["token"].astype(str).str.strip(),
            "tradingsymbol": frame["symbol"].astype(str).str.strip(),
            "name": frame.get("name", frame["symbol"]).astype(str),
            "exchange": exchange,
            "lotsize": pd.to_numeric(frame.get("lotsize", 1),
                                     errors="coerce").fillna(1).astype(int),
        }).drop_duplicates(subset=["symbol"]).reset_index(drop=True)
        if out.empty:
            raise ValueError(
                f"instrument master yielded no {exchange}{series_suffix} rows "
                "- source format may have changed")
        self._master = out
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            out.to_parquet(self.cache_dir / CACHE_FILE, index=False)
        logger.info("SmartAPI instruments loaded: %d %s equities",
                    len(out), exchange)
        return out

    def load_cached(self) -> pd.DataFrame:
        """Use a previously cached master (offline / restart path)."""
        if self.cache_dir is None:
            raise RuntimeError("no cache_dir configured")
        self._master = pd.read_parquet(self.cache_dir / CACHE_FILE)
        return self._master

    def ensure(self) -> pd.DataFrame:
        """Cached master if present, else fetch."""
        if self._master is not None:
            return self._master
        if self.cache_dir is not None \
                and (self.cache_dir / CACHE_FILE).exists():
            return self.load_cached()
        return self.fetch()

    # ------------------------------------------------------------- accessors

    def _lookup(self, symbol: str) -> Optional[pd.Series]:
        if self._master is None:
            return None
        match = self._master[self._master["symbol"] == symbol]
        return None if match.empty else match.iloc[0]

    def token_for(self, symbol: str) -> Optional[str]:
        row = self._lookup(symbol)
        return None if row is None else str(row["token"])

    def tradingsymbol_for(self, symbol: str) -> Optional[str]:
        row = self._lookup(symbol)
        return None if row is None else str(row["tradingsymbol"])

    def symbols(self) -> List[str]:
        return [] if self._master is None else sorted(self._master["symbol"])

    def sync_to_evidence(self) -> int:
        """Upsert reference rows into the evidence instruments table (reuse)."""
        if self.evidence_logger is None or self._master is None:
            return 0
        for row in self._master.itertuples(index=False):
            self.evidence_logger.upsert_instrument(row.symbol, name=row.name)
        return len(self._master)
