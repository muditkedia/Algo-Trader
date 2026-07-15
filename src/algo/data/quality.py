"""Data-quality gates (adapted from the archived crypto ``verify_data.py``).

Runs before any bars are admitted to the store. The crypto phase's hard lesson
(L-008) was that missing/!bad data must be LOUD, never silently reshaped - so
this returns a structured report separating blocking ERRORS (invalid OHLC, NaN,
non-positive prices) from WARNINGS (duplicates that were deduped, calendar gaps).
The ingestion engine quarantines a symbol whose report has errors; warnings are
recorded but admitted.

Equity adaptation: continuity is checked against the TRADING CALENDAR (a missing
non-session day is expected, not a gap), unlike the crypto 24/7 grid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from algo.data import ohlcv

ERROR = "error"
WARNING = "warning"


@dataclass
class QualityIssue:
    code: str
    severity: str
    detail: str


@dataclass
class QualityReport:
    symbol: str
    timeframe: str
    rows: int
    issues: List[QualityIssue] = field(default_factory=list)

    @property
    def errors(self) -> List[QualityIssue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> List[QualityIssue]:
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def ok(self) -> bool:
        """True when there are no blocking errors (warnings are acceptable)."""
        return not self.errors

    def add(self, code: str, severity: str, detail: str) -> None:
        self.issues.append(QualityIssue(code, severity, detail))


def check_ohlcv(frame: pd.DataFrame, symbol: str, timeframe: str,
                calendar=None) -> QualityReport:
    """Validate a raw OHLCV frame; return a structured report."""
    report = QualityReport(symbol=symbol, timeframe=timeframe,
                           rows=0 if ohlcv.is_empty(frame) else len(frame))
    if ohlcv.is_empty(frame):
        report.add("empty", ERROR, "no rows")
        return report

    missing = [c for c in ohlcv.OHLCV_COLUMNS if c not in frame.columns]
    if missing:
        report.add("missing_columns", ERROR, f"missing {missing}")
        return report

    df = frame.copy()
    df["date"] = pd.to_datetime(df["date"], utc=True)

    # --- duplicates / ordering (recoverable -> warnings) ---
    dups = int(df["date"].duplicated().sum())
    if dups:
        report.add("duplicate_timestamps", WARNING, f"{dups} duplicate rows")
    if not df["date"].is_monotonic_increasing:
        report.add("unsorted", WARNING, "timestamps not ascending")

    # --- OHLC validity (blocking) ---
    price = df[list(ohlcv.PRICE_COLUMNS)]
    nan_rows = int(price.isna().any(axis=1).sum() + df["volume"].isna().sum())
    if nan_rows:
        report.add("nan_values", ERROR, f"{nan_rows} rows with NaN OHLCV")
    if bool((df["high"] < df["low"]).any()):
        report.add("high_lt_low", ERROR, "high < low")
    if bool((df["high"] < df[["open", "close"]].max(axis=1)).any()):
        report.add("high_lt_body", ERROR, "high < max(open, close)")
    if bool((df["low"] > df[["open", "close"]].min(axis=1)).any()):
        report.add("low_gt_body", ERROR, "low > min(open, close)")
    if bool((price <= 0).any().any()):
        report.add("nonpositive_price", ERROR, "price <= 0")
    if bool((df["volume"] < 0).any()):
        report.add("negative_volume", ERROR, "volume < 0")

    # --- calendar-aware continuity (daily only, needs a calendar) ---
    if calendar is not None and timeframe == "1d":
        present = {ts.date() for ts in df["date"]}
        expected = calendar.sessions(df["date"].min().date(),
                                     df["date"].max().date())
        gaps = [d for d in expected if d not in present]
        if gaps:
            report.add("session_gaps", WARNING,
                       f"{len(gaps)} missing sessions "
                       f"(first {gaps[0]}, last {gaps[-1]})")
    return report
