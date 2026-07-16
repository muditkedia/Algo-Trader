"""KotakNeoInstruments - download and maintain the Kotak Neo instrument master.

Kotak's ``scrip_master(exchange_segment)`` returns either the parsed scrip list
or a ``{"filesPaths": [...csv urls...]}`` payload; this class handles both,
normalizes to a small canonical frame (symbol, instrument_token,
exchange_segment, name), caches it to parquet, and exposes the symbol -> token
lookup the quotes API needs. It can also sync the reference rows into the
evidence ``instruments`` table (reuse).

The HTTP downloader is injectable, so tests exercise everything with a mock and
no network. Nothing here is Kotak-order-related; it is pure reference data.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Callable, List, Optional

import pandas as pd

from algo.core.logging import get_logger

logger = get_logger("data.kotak.instruments")

COLUMNS = ("symbol", "instrument_token", "exchange_segment", "name")

# Candidate source-column names (Kotak transformed scrip master + fallbacks).
_TOKEN_CANDS = ("pSymbol", "instrument_token", "instrumentToken", "token", "pInstToken")
_SYMBOL_CANDS = ("pTrdSymbol", "trading_symbol", "tradingsymbol", "tradingSymbol",
                 "symbol", "pSymbolName")
_SEGMENT_CANDS = ("pExchSeg", "exchange_segment", "exchangeSegment", "segment")
_NAME_CANDS = ("pSymbolName", "name", "pInstName", "description", "pDesc")


def _default_downloader(url: str) -> str:  # pragma: no cover - network
    import requests
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def _pick(columns, candidates) -> Optional[str]:
    for cand in candidates:
        if cand in columns:
            return cand
    return None


class KotakNeoInstruments:
    def __init__(self, session, cache_dir=None,
                 downloader: Optional[Callable] = None,
                 evidence_logger=None) -> None:
        self.session = session
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.downloader = downloader or _default_downloader
        self.evidence_logger = evidence_logger
        self._master: Optional[pd.DataFrame] = None

    # ------------------------------------------------------------------ fetch

    def fetch(self, exchange_segment: str = "nse_cm") -> pd.DataFrame:
        """Download + normalize the scrip master for ``exchange_segment``."""
        resp = self.session.client.scrip_master(exchange_segment=exchange_segment)
        records = self._resolve_records(resp, exchange_segment)
        frame = self._normalize(records, exchange_segment)
        self._merge(frame)
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(self.cache_dir / f"kotak_{exchange_segment}.parquet",
                             index=False)
        logger.info("Kotak instruments loaded: %d for %s",
                    len(frame), exchange_segment)
        return frame

    def _resolve_records(self, resp, exchange_segment: str):
        if isinstance(resp, pd.DataFrame):
            return resp
        if isinstance(resp, dict) and resp.get("filesPaths"):
            url = next((u for u in resp["filesPaths"]
                        if exchange_segment in u), resp["filesPaths"][0])
            return pd.read_csv(io.StringIO(self.downloader(url)))
        if isinstance(resp, list):
            return pd.DataFrame(resp)
        raise ValueError(f"unrecognized scrip_master response: {type(resp)}")

    def _normalize(self, records, exchange_segment: str) -> pd.DataFrame:
        df = records if isinstance(records, pd.DataFrame) else pd.DataFrame(records)
        df.columns = [str(c).strip() for c in df.columns]
        cols = set(df.columns)
        tok, sym = _pick(cols, _TOKEN_CANDS), _pick(cols, _SYMBOL_CANDS)
        seg, nm = _pick(cols, _SEGMENT_CANDS), _pick(cols, _NAME_CANDS)
        if tok is None or sym is None:
            raise ValueError(
                "scrip master missing symbol/token columns; "
                f"got {sorted(cols)[:12]}...")
        out = pd.DataFrame({
            "symbol": df[sym].astype(str).str.strip(),
            "instrument_token": df[tok].astype(str).str.strip(),
            "exchange_segment": (df[seg].astype(str) if seg else exchange_segment),
            "name": (df[nm].astype(str) if nm else df[sym].astype(str)),
        })
        out = out[(out["symbol"] != "") & (out["instrument_token"] != "")]
        return out.drop_duplicates(subset=["symbol", "exchange_segment"]) \
                  .reset_index(drop=True)

    def _merge(self, frame: pd.DataFrame) -> None:
        if self._master is None:
            self._master = frame.copy()
        else:
            self._master = (pd.concat([self._master, frame], ignore_index=True)
                            .drop_duplicates(subset=["symbol", "exchange_segment"],
                                             keep="last")
                            .reset_index(drop=True))

    # ------------------------------------------------------------- accessors

    def load_cached(self, exchange_segment: str = "nse_cm") -> pd.DataFrame:
        if self.cache_dir is None:
            raise RuntimeError("no cache_dir configured")
        path = self.cache_dir / f"kotak_{exchange_segment}.parquet"
        frame = pd.read_parquet(path)
        self._merge(frame)
        return frame

    def token_for(self, symbol: str,
                  exchange_segment: Optional[str] = None) -> Optional[str]:
        if self._master is None:
            return None
        match = self._master[self._master["symbol"] == symbol]
        if exchange_segment is not None:
            match = match[match["exchange_segment"] == exchange_segment]
        return None if match.empty else str(match.iloc[0]["instrument_token"])

    def symbols(self, exchange_segment: Optional[str] = None) -> List[str]:
        if self._master is None:
            return []
        frame = self._master
        if exchange_segment is not None:
            frame = frame[frame["exchange_segment"] == exchange_segment]
        return sorted(frame["symbol"].tolist())

    def sync_to_evidence(self) -> int:
        """Upsert basic reference rows into the evidence instruments table."""
        if self.evidence_logger is None or self._master is None:
            return 0
        for row in self._master.itertuples(index=False):
            self.evidence_logger.upsert_instrument(row.symbol, name=row.name)
        return len(self._master)
