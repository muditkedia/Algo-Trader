"""Store audit core - integrity, gap and completeness checks (plan section 6).

Pure functions over a MarketDataStore; the CLI wrapper is
``scripts/store_audit.py``. Design choices:

* Market-wide short sessions (median bars across symbols below the expected
  count) are classified as SPECIAL sessions automatically - Muhurat days,
  exchange drills and half-days identify themselves, so there is no
  hand-maintained date list to go stale across a 2016-2026 history. Only
  symbol-specific shortfalls on NORMAL sessions are anomalies.
* Missing sessions are judged per symbol AFTER its own listing start
  (late listings are honest, not gaps).
* Context completeness: in any timeframe where the NIFTY50 series exists,
  every equity session on/after its start must have context bars - market
  features must never silently see NaN.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import pandas as pd

from algo.data.store import MarketDataStore

CONTEXT_SYMBOLS = ("NIFTY50", "BANKNIFTY", "INDIAVIX")
EXPECTED_BARS = {"1m": 375, "5m": 75, "15m": 25, "1h": 7, "1d": 1}
#: a session with fewer than this fraction of expected bars counts as short
SHORT_FRACTION = 0.8
#: more than this many missing sessions post-listing is a hard failure
MISSING_FAIL = 5


def _dates(store: MarketDataStore, symbol: str, tf: str) -> pd.Series:
    return pd.read_parquet(store._path(symbol, tf), columns=["date"])["date"]


def _sessions(dates: pd.Series) -> pd.Series:
    return dates.dt.normalize()


def audit_timeframe(store: MarketDataStore, tf: str) -> dict:
    """Audit one timeframe; returns {timeframe, symbols, failures, warnings,
    special_sessions, coverage, union_sessions, calendar_start/end}."""
    out = {"timeframe": tf, "symbols": 0, "failures": [], "warnings": [],
           "special_sessions": [], "coverage": {}, "union_sessions": 0}
    symbols = store.symbols(tf)
    out["symbols"] = len(symbols)
    if not symbols:
        return out
    expected = EXPECTED_BARS.get(tf)
    per_session: Dict[str, pd.Series] = {}
    for sym in symbols:
        dates = _dates(store, sym, tf)
        if dates.duplicated().any():
            out["failures"].append(f"{sym}: duplicate timestamps")
        if not dates.is_monotonic_increasing:
            out["failures"].append(f"{sym}: non-monotonic dates")
        per_session[sym] = _sessions(dates).value_counts().sort_index()
        out["coverage"][sym] = {"start": str(dates.min()),
                                "end": str(dates.max()),
                                "rows": int(len(dates))}

    calendar = sorted(set().union(*[set(c.index)
                                    for c in per_session.values()]))
    out["union_sessions"] = len(calendar)
    out["calendar_start"] = str(calendar[0].date())
    out["calendar_end"] = str(calendar[-1].date())

    if expected and expected > 1:
        counts = pd.DataFrame(per_session).reindex(calendar)
        median = counts.median(axis=1, skipna=True)
        special = median[median < SHORT_FRACTION * expected]
        out["special_sessions"] = [str(d.date()) for d in special.index]
        normal = counts.loc[median >= SHORT_FRACTION * expected]
        short = (normal < SHORT_FRACTION * expected)
        for sym, n in short.sum()[lambda s: s > 0].items():
            (out["warnings"] if n <= 5 else out["failures"]).append(
                f"{sym}: {int(n)} short session(s) on normal trading days")

    for sym, sess in per_session.items():
        own = set(sess.index)
        start = min(own)
        missing = len({c for c in calendar if c >= start} - own)
        if missing > MISSING_FAIL:
            out["failures"].append(
                f"{sym}: {missing} missing sessions post-listing")
        elif missing > 0:
            out["warnings"].append(
                f"{sym}: {missing} missing session(s) post-listing")
    return out


def cross_timeframe(store: MarketDataStore, fine: str,
                    coarse: str, tolerance: int = 3) -> List[str]:
    """Warnings where a symbol's fine/coarse session sets disagree beyond
    ``tolerance`` within their overlapping range (provider depth differs
    legitimately per interval)."""
    notes = []
    for sym in sorted(set(store.symbols(fine)) & set(store.symbols(coarse))):
        f = set(_sessions(_dates(store, sym, fine)).unique())
        c = set(_sessions(_dates(store, sym, coarse)).unique())
        overlap_start = max(min(f), min(c))
        diff = len({d for d in f if d >= overlap_start}
                   ^ {d for d in c if d >= overlap_start})
        if diff > tolerance:
            notes.append(f"{sym}: {fine}/{coarse} session sets differ by "
                         f"{diff} within the overlapping range")
    return notes


def context_completeness(store: MarketDataStore,
                         tf: str) -> Tuple[List[str], List[str]]:
    """(failures, warnings) for market-context coverage in ``tf``."""
    failures: List[str] = []
    warnings: List[str] = []
    symbols = store.symbols(tf)
    equities = [s for s in symbols if s not in CONTEXT_SYMBOLS]
    if not equities:
        return failures, warnings
    if "NIFTY50" not in symbols:
        warnings.append(f"{tf}: no NIFTY50 context series present")
        return failures, warnings
    ctx = set(_sessions(_dates(store, "NIFTY50", tf)).unique())
    ctx_start = min(ctx)
    union: set = set()
    for sym in equities:
        union |= set(_sessions(_dates(store, sym, tf)).unique())
    uncovered = sorted(d for d in union if d >= ctx_start and d not in ctx)
    before = sorted(d for d in union if d < ctx_start)
    if uncovered:
        failures.append(
            f"{tf}: {len(uncovered)} equity session(s) lack NIFTY50 bars "
            f"(first: {uncovered[0].date()}, last: {uncovered[-1].date()})")
    if before:
        warnings.append(
            f"{tf}: {len(before)} equity session(s) predate the NIFTY50 "
            f"series start {ctx_start.date()}")
    return failures, warnings
