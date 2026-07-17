"""IngestionEngine - provider -> quality gate -> store, incrementally.

Two entry points:

* ``full_import``        - fetch an explicit [start, end] window per symbol.
* ``incremental_update`` - fetch only what the store is missing: the tail
                           window starts one bar after the stored ``last_date``
                           (a symbol already up to date is a no-op), and when a
                           requested history start is given, a HEAD gap (stored
                           coverage beginning after the requested start) is
                           backfilled too.

The head-gap backfill exists because of a real defect (D-029): the original
implementation only ever extended coverage FORWARD, so a symbol first seeded
with a narrow test window could never be backfilled - the wider re-run reported
it ``up_to_date``, which was true of the tail and silently false of the
requested window. That is exactly how RELIANCE and TCS lost 2023-2024 on the
daily timeframe while 97 other symbols were complete.

Duplicate protection is two-layered: the incremental windows never re-request
stored bars, AND the store upserts on timestamp (last wins). Running an update
twice therefore downloads nothing the second time and can never duplicate a
candle. A symbol whose fetched data fails the quality gate is QUARANTINED (not
stored) and reported - loud, never silent (L-008).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from algo.core.logging import get_logger
from algo.data import ohlcv
from algo.data.quality import QualityReport, check_ohlcv, invalid_row_mask
from algo.data.providers.base import DataProvider
from algo.data.store import MarketDataStore

logger = get_logger("data.ingest")


@dataclass
class SymbolResult:
    symbol: str
    timeframe: str
    status: str                 # ok | backfilled | up_to_date | empty | quarantined
    fetched: int = 0
    added: int = 0
    detail: str = ""
    quality: Optional[QualityReport] = None


@dataclass
class IngestionReport:
    timeframe: str
    results: List[SymbolResult] = field(default_factory=list)

    def summary(self) -> dict:
        by_status: dict = {}
        added = fetched = 0
        for r in self.results:
            by_status[r.status] = by_status.get(r.status, 0) + 1
            added += r.added
            fetched += r.fetched
        return {"symbols": len(self.results), "by_status": by_status,
                "rows_fetched": fetched, "rows_added": added}


class IngestionEngine:
    def __init__(self, store: MarketDataStore, provider: DataProvider,
                 calendar=None, max_invalid_row_pct: float = 0.001,
                 max_invalid_rows: int = 5) -> None:
        """Tolerance for ISOLATED bad bars.

        Real broker feeds contain rare tick glitches (observed on SmartAPI:
        ~1 impossible bar per 20,000). Discarding a symbol's entire multi-year
        history over one such bar loses far more than it protects. So a glitch
        is DROPPED - always counted and logged, never silently reshaped
        (L-008) - and the rest admitted; a systematically broken feed is still
        quarantined whole.

        Tolerance is ``max(max_invalid_rows, max_invalid_row_pct x rows)``. The
        absolute floor matters because a percentage alone punishes SHORT
        series: one bad bar in an 877-row daily history is 0.11% and would trip
        a 0.1% rule, while the same defect in 15m data is 0.005%. The defect is
        identical; only the sampling differs.

        Set both to 0 for strict all-or-nothing behaviour.
        """
        self.store = store
        self.provider = provider
        self.calendar = calendar
        self.max_invalid_row_pct = max_invalid_row_pct
        self.max_invalid_rows = max_invalid_rows

    # ------------------------------------------------------------------ full

    def full_import(self, symbols: List[str], timeframe: str,
                    start, end) -> IngestionReport:
        report = IngestionReport(timeframe=timeframe)
        for symbol in symbols:
            report.results.append(
                self._ingest(symbol, timeframe, start, end))
        logger.info("full_import %s: %s", timeframe, report.summary())
        return report

    # ----------------------------------------------------------- incremental

    def incremental_update(self, symbols: List[str], timeframe: str,
                           end=None, start_if_empty=None,
                           default_lookback_days: int = 365) -> IngestionReport:
        """Fetch what the store is missing for each symbol.

        ``start_if_empty`` is the requested history start. It seeds a symbol
        with no data, AND backfills a symbol whose stored coverage begins later
        than it (status ``backfilled``) - coverage must satisfy the request at
        both ends, not just the tail (D-029). Callers that pass no start (the
        paper engine's rolling top-up) get tail-only behaviour, unchanged.
        """
        end = ohlcv.day_end(end) if end is not None \
            else pd.Timestamp.now(tz="UTC")
        step = pd.Timedelta(minutes=ohlcv.timeframe_minutes(timeframe))
        report = IngestionReport(timeframe=timeframe)
        for symbol in symbols:
            cov = self.store.coverage(symbol, timeframe)
            if cov is not None:
                # head gap: requested history begins before the stored data
                if start_if_empty is not None:
                    requested = pd.Timestamp(start_if_empty, tz="UTC")
                    head_end = cov["start"] - step
                    if requested <= head_end:
                        head = self._ingest(symbol, timeframe, requested,
                                            head_end)
                        if head.status == "ok":
                            head.status = "backfilled"
                            head.detail = (f"head gap {requested.date()} -> "
                                           f"{head_end.date()}" +
                                           (f"; {head.detail}" if head.detail
                                            else ""))
                        report.results.append(head)
                start = cov["end"] + step
            elif start_if_empty is not None:
                start = pd.Timestamp(start_if_empty, tz="UTC")
            else:
                start = end - pd.Timedelta(days=default_lookback_days)
            if start > end:
                report.results.append(SymbolResult(
                    symbol, timeframe, "up_to_date",
                    detail=f"stored through {cov['end'] if cov else None}"))
                continue
            report.results.append(self._ingest(symbol, timeframe, start, end))
        logger.info("incremental_update %s: %s", timeframe, report.summary())
        return report

    # --------------------------------------------------------------- per-symbol

    def _ingest(self, symbol: str, timeframe: str, start, end) -> SymbolResult:
        try:
            fetched = self.provider.fetch_ohlcv(symbol, timeframe, start, end)
        except NotImplementedError:
            raise
        except Exception as exc:  # a provider failure must not abort the batch
            logger.warning("fetch failed %s/%s: %s", symbol, timeframe, exc)
            return SymbolResult(symbol, timeframe, "quarantined",
                                detail=f"fetch error: {exc}")
        if ohlcv.is_empty(fetched):
            return SymbolResult(symbol, timeframe, "empty",
                                detail="provider returned no rows")
        report = check_ohlcv(fetched, symbol, timeframe, self.calendar)
        dropped = 0
        if not report.ok:
            reasons = "; ".join(f"{i.code}:{i.detail}" for i in report.errors)
            mask = invalid_row_mask(fetched)
            rate = float(mask.mean()) if len(mask) else 1.0
            n_bad = int(mask.sum())
            allowance = max(self.max_invalid_rows,
                            int(self.max_invalid_row_pct * len(fetched)))
            if 0 < n_bad <= allowance:
                # isolated glitch bars: drop exactly those, loudly, keep the rest
                dropped = int(mask.sum())
                logger.warning(
                    "%s/%s: dropping %d invalid bar(s) (%.4f%% of %d) then "
                    "admitting the rest - %s", symbol, timeframe, dropped,
                    rate * 100, len(fetched), reasons)
                fetched = fetched[~mask].reset_index(drop=True)
                report = check_ohlcv(fetched, symbol, timeframe, self.calendar)
            if not report.ok:
                reasons = "; ".join(f"{i.code}:{i.detail}"
                                    for i in report.errors)
                logger.warning("QUARANTINE %s/%s (%.3f%% bad rows): %s",
                               symbol, timeframe, rate * 100, reasons)
                return SymbolResult(symbol, timeframe, "quarantined",
                                    fetched=len(fetched), detail=reasons,
                                    quality=report)
        added = self.store.write(symbol, timeframe, fetched)
        detail = f"dropped {dropped} invalid bar(s)" if dropped else ""
        return SymbolResult(symbol, timeframe, "ok", fetched=len(fetched),
                            added=added, detail=detail, quality=report)
