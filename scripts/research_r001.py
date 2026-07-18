"""R-001 execution: three short-horizon hypothesis families (Phase 14).

Pre-registered in research/PREREGISTRATION_R001.md. Uses the FROZEN hypothesis
framework (compile_hypothesis) and the FROZEN measurement gate. The three entry
rules are defined LOCALLY here (research-specific logic belongs in the research
script, not the frozen component library), and each is causal - it uses only the
current and prior bars / the known calendar.

    .venv/Scripts/python scripts/research_r001.py --symbols-file nifty500.txt

Persists evidence to the production DB (a new evaluation generation) and writes a
league table with the multiple-comparison count. Deterministic.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from algo.core.costs import NseEquityCostModel
from algo.core.logging import configure, get_logger
from algo.data.store import MarketDataStore
from algo.evidence.database import EvidenceDB
from algo.evidence.logger import EvidenceLogger
from algo.research import reporting
from algo.research.engine import ResearchEngine
from algo.research.hypothesis import Hypothesis, compile_hypothesis

logger = get_logger("scripts.r001")
ROOT = Path("user_data")
REPORT = ROOT / "backtest_results" / "reports" / "r001_league.md"


# --------------------------------------------------- causal entry rules

def month_start_entry(df: pd.DataFrame) -> pd.Series:
    """First trading bar of each calendar month (edge-triggered, causal)."""
    month = df["date"].dt.tz_localize(None).dt.to_period("M")
    return (month != month.shift(1)).fillna(False)


def gap_fade_entry(df: pd.DataFrame) -> pd.Series:
    """Sharp overnight DOWN-gap (<= -2%); one signal per gap event (causal)."""
    gap = df["open"] / df["close"].shift(1) - 1.0
    return (gap <= -0.02).fillna(False)


def expiry_entry(df: pd.DataFrame) -> pd.Series:
    """First bar within 2 business days on/before the last Thursday of the
    month (monthly F&O expiry window; edge-triggered, causal - the expiry date
    is a known calendar fact)."""
    dates = df["date"].dt.tz_localize(None).dt.normalize()
    month_end = dates + pd.offsets.MonthEnd(0)
    # last Thursday = month-end minus the days back to weekday 3 (Thu)
    offset = (month_end.dt.weekday - 3) % 7
    last_thu = month_end - pd.to_timedelta(offset, unit="D")
    to_expiry = np.busday_count(dates.to_numpy("datetime64[D]"),
                                last_thu.to_numpy("datetime64[D]"))
    in_window = pd.Series((to_expiry >= 0) & (to_expiry <= 2), index=df.index)
    return (in_window & ~in_window.shift(1, fill_value=False)).fillna(False)


def hypotheses() -> list:
    return [
        Hypothesis(
            name="r001_month_start", family="calendar",
            hypothesis="Buy the first trading session of the month (SIP/salary "
                       "inflows land at month-start); hold ~1 week.",
            entry=month_start_entry, horizon_bars=(3, 5, 7), max_hold_bars=7,
            min_history=25),
        Hypothesis(
            name="r001_gap_fade", family="microstructure",
            hypothesis="Buy after a sharp overnight down-gap (<=-2%); the "
                       "overreaction partially reverses over a few sessions.",
            entry=gap_fade_entry, horizon_bars=(3, 5), max_hold_bars=5,
            min_history=25),
        Hypothesis(
            name="r001_expiry", family="calendar/derivatives",
            hypothesis="Buy ~2 sessions before monthly F&O expiry (last "
                       "Thursday); hold through expiry-week rollover flows.",
            entry=expiry_entry, horizon_bars=(3, 5, 7), max_hold_bars=7,
            min_history=25),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols-file", default="nifty500.txt")
    parser.add_argument("--store", default=str(ROOT / "data" / "nse"))
    parser.add_argument("--db", default=str(ROOT / "evidence" / "evidence.db"))
    args = parser.parse_args()
    configure(level=logging.WARNING)

    store = MarketDataStore(args.store)
    symbols = [s.strip().upper() for s in Path(args.symbols_file).read_text()
               .splitlines() if s.strip() and not s.startswith("#")]
    symbols = [s for s in symbols if s in set(store.symbols("1d"))]

    db = EvidenceDB(args.db)
    log = EvidenceLogger(db)
    for sym in symbols:
        log.upsert_instrument(sym)
    engine = ResearchEngine(db, store=store, cost_model=NseEquityCostModel())

    specs = hypotheses()
    strategies = [compile_hypothesis(s) for s in specs]
    print(f"R-001: {len(strategies)} pre-registered hypotheses on "
          f"{len(symbols)} symbols\n")

    verdicts = engine.research_all(
        strategies, symbols, benchmarks=True,
        on_verdict=lambda v: print(
            f"  {v.strategy:18} recorded={v.n_recorded:6d} "
            f"labeled={v.n_labeled:6d} -> {v.verdict}"))
    table = engine.league_table(verdicts)

    print("\n" + "=" * 100)
    print(f"R-001 LEAGUE TABLE   (hypotheses tested this phase: {len(specs)}; "
          "lifetime project total: 30)")
    print("=" * 100)
    print(reporting.league_table_text(table))
    print("\nVERDICT DETAIL\n")
    print(reporting.verdict_detail_text(verdicts))
    print("\nMULTIPLE-COMPARISON CONTEXT: 3 independent hypotheses tested; "
          "~0.15 false PASSes expected by chance. Any PASS is PROVISIONAL "
          "pending pre-registered out-of-sample confirmation (RESEARCH_STANDARDS "
          "§3) and is NOT promoted this phase.")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(reporting.league_table_markdown(
        table, verdicts, data_note=f"R-001 short-horizon hypotheses "
        f"({len(symbols)} symbols; 3 tested, 30 lifetime)",
        title="R-001 league table"), encoding="utf-8")
    print(f"\nreport: {REPORT}")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
