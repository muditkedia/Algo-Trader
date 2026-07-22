"""Measurement run - every candidate through the identical research pipeline.

Runs the complete evidence loop end to end, per strategy:

    record every signal with its confidence -> label outcomes (incl. simulated
    managed trades) -> measure entry edge (D-007 gate) -> evaluate (metric
    battery) -> cost sensitivity -> confidence calibration -> league table with
    PASS / BORDERLINE / FAIL verdicts.

The loop itself lives in ``ResearchEngine.research_all``; this script only
chooses the data, the universe and where the report lands. Strategies are
discovered from ``algo.strategies.library``, so a new candidate is measured by
adding its module - nothing here needs to know it exists.

DATA HONESTY: without ``--store-dir``/``--csv-root`` this runs on the synthetic
corpus. Verdicts on synthetic data validate the MACHINERY - they are not market
verdicts. The pipeline's discrimination is proven separately by the test
controls (planted edge -> PASS, pure noise -> FAIL).

Usage:
    .venv/Scripts/python scripts/run_measurement.py                 # synthetic
    .venv/Scripts/python scripts/run_measurement.py --csv-root DIR  # CSV import
    .venv/Scripts/python scripts/run_measurement.py \
        --store-dir user_data/data/nse                              # real store
        [--symbols RELIANCE,TCS | --symbols-file file]
        [--strategies orb_5m]              # measure a subset, same bars

With ``--store-dir`` (e.g. after scripts/download_history.py) the REAL evidence
DB (user_data/evidence/evidence.db) is used, verdicts are persisted as strategy
lifecycle statuses (PASS -> measured, FAIL -> rejected), and nothing is wiped -
this is the production measurement path the paper engine gates on.

There is deliberately NO flag to change a strategy's measurement horizon: it is
pre-registered in the strategy's own ``meta`` and versioned with it. Re-running
a candidate at a horizon picked after seeing its verdict is the curve-fitting
D-026 forbids, and a CLI flag is exactly how that would happen by accident.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

import pandas as pd

from algo.core.calendar import StaticCalendar
from algo.core.costs import NseEquityCostModel
from algo.core.logging import configure, get_logger
from algo.data.providers.csv_provider import CsvDataProvider
from algo.data.providers.synthetic import SyntheticDataProvider
from algo.data.store import MarketDataStore
from algo.evidence.database import EvidenceDB
from algo.evidence.logger import EvidenceLogger
from algo.research import reporting
from algo.research.engine import ResearchEngine
from algo.strategies.library import ALL_STRATEGIES

logger = get_logger("scripts.measurement")

ROOT = Path("user_data")
STORE_DIR = ROOT / "data" / "synthetic_measurement"
DB_PATH = ROOT / "evidence" / "phase5_measurement.db"
REPORT = ROOT / "backtest_results" / "reports" / "phase5_league_table.md"


def build_synthetic_data(store: MarketDataStore, n_symbols: int) -> list:
    """Session-aware synthetic corpus deep enough for every strategy."""
    sessions = pd.bdate_range("2022-09-01", "2024-03-28")
    calendar = StaticCalendar([d.date() for d in sessions])
    provider = SyntheticDataProvider(seed=42, calendar=calendar)
    symbols = [f"SYN{i:02d}" for i in range(n_symbols)]
    for symbol in symbols:
        store.write(symbol, "1d", provider.fetch_ohlcv(
            symbol, "1d", "2022-09-01", "2024-03-28"))       # ~410 sessions
        store.write(symbol, "1h", provider.fetch_ohlcv(
            symbol, "1h", "2024-01-01", "2024-03-28"))       # ~60 sessions
        store.write(symbol, "15m", provider.fetch_ohlcv(
            symbol, "15m", "2024-02-01", "2024-03-28"))      # ~40 sessions
    return symbols


def import_csv_data(store: MarketDataStore, csv_root: str) -> list:
    """Real-data path: import a broker/vendor CSV export instead."""
    provider = CsvDataProvider(csv_root)
    symbols = provider.list_symbols()
    for symbol in symbols:
        for tf in ("1d", "1h", "15m"):
            bars = provider.fetch_ohlcv(symbol, tf, "2000-01-01", "2100-01-01")
            if not bars.empty:
                store.write(symbol, tf, bars)
    return symbols


def select_strategies(names: str = None) -> list:
    """Instantiate the discovered library, optionally filtered by name.

    A subset runs the IDENTICAL pipeline over the IDENTICAL bars - it only
    skips work. That matters for throughput: iterating on one new candidate
    should not re-measure every strategy already on record.
    """
    available = {cls.meta.name: cls for cls in ALL_STRATEGIES}
    if not names:
        return [cls() for cls in ALL_STRATEGIES]
    wanted = [n.strip() for n in names.split(",") if n.strip()]
    unknown = [n for n in wanted if n not in available]
    if unknown:
        raise SystemExit(f"unknown strategy: {', '.join(unknown)}. "
                         f"Available: {', '.join(sorted(available))}")
    return [available[n]() for n in wanted]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="12",
                        help="synthetic: count; with --store-dir: "
                             "comma-separated symbol list")
    parser.add_argument("--symbols-file", default=None,
                        help="file with one symbol per line (with --store-dir)")
    parser.add_argument("--csv-root", default=None,
                        help="import real CSV data instead of synthetic")
    parser.add_argument("--store-dir", default=None,
                        help="measure an EXISTING store (e.g. the SmartAPI "
                             "download at user_data/data/nse)")
    parser.add_argument("--strategies", default=None,
                        help="comma-separated subset to measure "
                             "(default: every discovered strategy)")
    args = parser.parse_args()
    configure(level=logging.WARNING)

    real_run = bool(args.store_dir)
    if real_run:
        store = MarketDataStore(args.store_dir)
        if args.symbols_file:
            symbols = [s.strip().upper() for s in
                       Path(args.symbols_file).read_text().splitlines()
                       if s.strip() and not s.startswith("#")]
        elif args.symbols and not args.symbols.isdigit():
            symbols = [s.strip().upper() for s in args.symbols.split(",")
                       if s.strip()]
        else:
            symbols = sorted(set().union(*(set(store.symbols(tf)) for tf in
                                           ("15m", "1h", "1d"))))
        data_note = f"REAL data from {args.store_dir}"
        db_path = ROOT / "evidence" / "evidence.db"     # production DB, kept
    else:
        # fresh, reproducible synthetic/CSV run in the scratch DB
        shutil.rmtree(STORE_DIR, ignore_errors=True)
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        DB_PATH.unlink(missing_ok=True)
        store = MarketDataStore(STORE_DIR)
        if args.csv_root:
            symbols = import_csv_data(store, args.csv_root)
            data_note = f"REAL data imported from {args.csv_root}"
        else:
            symbols = build_synthetic_data(store, int(args.symbols))
            data_note = ("SYNTHETIC corpus - verdicts validate the machinery, "
                         "NOT the strategies' market worth")
        db_path = DB_PATH
    print(f"data: {len(symbols)} symbols | {data_note}\n")

    db = EvidenceDB(db_path)
    log = EvidenceLogger(db)
    for symbol in symbols:
        log.upsert_instrument(symbol)
    engine = ResearchEngine(db, store=store, cost_model=NseEquityCostModel())

    strategies = select_strategies(args.strategies)
    print(f"strategies: {', '.join(s.name for s in strategies)}\n")

    # The whole evidence loop, per strategy, in one call. Benchmarks on: every
    # strategy is judged against buy&hold and random-entry baselines (D-031).
    verdicts = engine.research_all(
        strategies, symbols, benchmarks=True,
        on_verdict=lambda v: print(
            f"  {v.strategy:14} signals recorded: {v.n_recorded:5d}  "
            f"outcomes labeled: {v.n_labeled:5d}  -> {v.verdict}"))
    table = engine.league_table(verdicts)

    print("\n" + "=" * 100)
    print("LEAGUE TABLE  (" + data_note + ")")
    print("=" * 100)
    print(reporting.league_table_text(table))
    print("\nVERDICT DETAIL\n")
    print(reporting.verdict_detail_text(verdicts))

    # Persist verdicts as lifecycle statuses (the paper engine gates on these).
    # Only real-data verdicts advance a strategy; synthetic runs record only.
    status_map = {"PASS": "measured", "FAIL": "rejected",
                  "BORDERLINE": "draft", "INCONCLUSIVE": "draft"}
    for verdict in verdicts:
        strategy_id = log.register_strategy(
            verdict.strategy,
            next(s.meta.version for s in strategies
                 if s.name == verdict.strategy))
        if real_run:
            log.set_strategy_status(
                strategy_id, status_map[verdict.verdict],
                reason=f"measurement {verdict.verdict}: "
                       + "; ".join(verdict.reasons)[:400])
        else:
            log.set_strategy_status(
                strategy_id, "draft",
                reason=f"synthetic-run {verdict.verdict} (machinery check, "
                       "not a market verdict)")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(reporting.league_table_markdown(
        table, verdicts, data_note=data_note,
        title="Measurement league table"), encoding="utf-8")
    print(f"\nreport: {REPORT}")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
