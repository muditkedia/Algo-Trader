"""UniverseManager - maintain the tradable universe and its eligibility metrics.

Responsibilities:

  * symbol master - register/track instruments (sector, listing, ...) in the
    evidence ``instruments`` table (reused from Phase 1).
  * metrics frame - compute, point-in-time, the per-symbol metrics the filters
    need (price, average volume, average traded value) from the market-data
    store, joined with instrument attributes (sector, market cap when supplied).
  * eligibility - apply the configured ``Universe`` filter pipeline and return
    the eligible symbols with drop attribution.

Market cap is "if available": it is not part of the Phase-1 instruments schema
(it is not static), so it flows in via the optional ``attributes`` frame rather
than forcing a schema migration.
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from algo.core.logging import get_logger
from algo.universe.filters import (
    COL_AVG_TRADED_VALUE, COL_AVG_VOLUME, COL_MARKET_CAP, COL_PRICE, COL_SECTOR,
)
from algo.universe.base import Filter
from algo.universe.universe import Universe, UniverseResult

logger = get_logger("universe.manager")


class UniverseManager:
    def __init__(self, store, evidence_logger=None, calendar=None) -> None:
        self.store = store
        self.evidence_logger = evidence_logger
        self.calendar = calendar

    # -------------------------------------------------------- symbol master

    def register_symbols(self, records: List[dict]) -> int:
        """Upsert instrument reference rows (needs an evidence logger)."""
        if self.evidence_logger is None:
            raise RuntimeError("register_symbols requires an evidence logger")
        for rec in records:
            symbol = rec["symbol"]
            self.evidence_logger.upsert_instrument(
                symbol, **{k: v for k, v in rec.items() if k != "symbol"})
        return len(records)

    def load_attributes(self) -> pd.DataFrame:
        """Instrument attributes (sector, ...) from the evidence table, indexed
        by symbol. Empty frame when no logger/db is available."""
        if self.evidence_logger is None:
            return pd.DataFrame()
        rows = self.evidence_logger.db.connection.execute(
            "SELECT symbol, sector, industry, fo_eligible FROM instruments"
        ).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([dict(r) for r in rows]).set_index("symbol")

    # --------------------------------------------------------- metrics frame

    def metrics_frame(self, symbols: List[str], as_of=None,
                      timeframe: str = "1d", lookback: int = 20,
                      attributes: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        """Per-symbol eligibility metrics as of ``as_of`` (point-in-time).

        Uses only bars up to ``as_of`` (no lookahead). Joins ``attributes``
        (sector/market_cap/...) when supplied, else the evidence instruments
        table when a logger is available.
        """
        records = []
        for symbol in symbols:
            bars = self.store.read(symbol, timeframe, end=as_of)
            if bars.empty:
                records.append({"symbol": symbol, COL_PRICE: float("nan"),
                                COL_AVG_VOLUME: float("nan"),
                                COL_AVG_TRADED_VALUE: float("nan")})
                continue
            window = bars.tail(lookback)
            records.append({
                "symbol": symbol,
                COL_PRICE: float(window["close"].iloc[-1]),
                COL_AVG_VOLUME: float(window["volume"].mean()),
                COL_AVG_TRADED_VALUE: float(
                    (window["close"] * window["volume"]).mean()),
            })
        frame = pd.DataFrame(records).set_index("symbol")

        if attributes is None:
            attributes = self.load_attributes()
        if attributes is not None and not attributes.empty:
            frame = frame.join(attributes, how="left")
        if COL_SECTOR not in frame.columns:
            frame[COL_SECTOR] = pd.NA
        if COL_MARKET_CAP not in frame.columns:
            frame[COL_MARKET_CAP] = float("nan")
        return frame

    # ------------------------------------------------------------- eligible

    def eligible(self, symbols: List[str], filters: List[Filter],
                 as_of=None, timeframe: str = "1d", lookback: int = 20,
                 attributes: Optional[pd.DataFrame] = None) -> UniverseResult:
        """Build the metrics frame and apply the filter pipeline."""
        frame = self.metrics_frame(symbols, as_of=as_of, timeframe=timeframe,
                                   lookback=lookback, attributes=attributes)
        result = Universe(filters).apply(frame)
        logger.info("universe eligible: %d/%d symbols as_of %s",
                    result.n_kept, result.n_in, as_of)
        return result
