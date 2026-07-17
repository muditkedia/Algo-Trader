"""Phase 5 measurement run - all six strategies through the identical pipeline.

Runs the complete evidence loop end to end:

    build data -> measure entry edge (D-007 gate) -> record every signal with
    its confidence -> label outcomes (incl. simulated managed trades) ->
    evaluate (metric battery) -> cost sensitivity -> confidence calibration ->
    league table with PASS / BORDERLINE / FAIL verdicts.

DATA HONESTY: until real NSE history is imported (CSV) this runs on the
synthetic corpus. Verdicts on synthetic data validate the MACHINERY - they are
not market verdicts. The pipeline's discrimination is proven separately by the
test controls (planted edge -> PASS, pure noise -> FAIL). Point ``--csv-root``
at a real export to produce real verdicts with zero code changes.

Usage:
    .venv/Scripts/python scripts/run_measurement.py                 # synthetic
    .venv/Scripts/python scripts/run_measurement.py --csv-root DIR  # CSV import
    .venv/Scripts/python scripts/run_measurement.py \
        --store-dir user_data/data/nse                              # real store
        [--symbols RELIANCE,TCS | --symbols-file file]

With ``--store-dir`` (e.g. after scripts/download_history.py) the REAL evidence
DB (user_data/evidence/evidence.db) is used, verdicts are persisted as strategy
lifecycle statuses (PASS -> measured, FAIL -> rejected), and nothing is wiped -
this is the production measurement path the paper engine gates on.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from algo.core.calendar import StaticCalendar
from algo.core.costs import NseEquityCostModel, Product
from algo.core.logging import configure, get_logger
from algo.data.providers.csv_provider import CsvDataProvider
from algo.data.providers.synthetic import SyntheticDataProvider
from algo.data.store import MarketDataStore
from algo.evidence.database import EvidenceDB
from algo.evidence.logger import EvidenceLogger
from algo.evidence.models import Disposition, Mode, Signal
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


def record_historical_signals(engine: ResearchEngine, log: EvidenceLogger,
                              strategy, symbols: list) -> int:
    """Record every historical firing bar as an evidence signal WITH the
    strategy's confidence at that bar (indicators are causal, so confidence on
    the truncated frame equals live confidence)."""
    strategy_id = log.register_strategy(strategy.name, strategy.meta.version)
    recorded = 0
    for symbol in symbols:
        bars = engine.store.read(symbol, strategy.meta.timeframe)
        if bars.empty or len(bars) < strategy.min_history():
            continue
        prepared = strategy.prepare(bars)
        fired = strategy.entry_signal(prepared)
        for i in np.flatnonzero(fired.to_numpy(bool)):
            ts = str(pd.Timestamp(prepared["date"].iloc[i]))
            if log.signal_exists(strategy_id, symbol, ts, Mode.BACKTEST.value):
                continue
            conf = strategy.confidence(prepared.iloc[:int(i) + 1])
            log.record_signal(Signal(
                ts=ts, symbol=symbol, strategy_id=strategy_id,
                direction=strategy.meta.direction.value,
                mode=Mode.BACKTEST.value,
                disposition=Disposition.RECORDED_ONLY.value,
                disposition_reason="phase5 measurement replay",
                entry_price=float(prepared["close"].iloc[i]),
                confidence_score=conf.score,
                confidence_components=conf.components or None))
            recorded += 1
    return recorded


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

    strategies = [cls() for cls in ALL_STRATEGIES]
    # 1) record every historical signal (with confidence) + label outcomes
    for strategy in strategies:
        strategy_id = log.register_strategy(strategy.name,
                                            strategy.meta.version)
        n_signals = record_historical_signals(engine, log, strategy, symbols)
        product = (Product.INTRADAY
                   if strategy.meta.holding_scope.value == "intraday"
                   else Product.DELIVERY)
        n_labeled = engine.label_outcomes(strategy_id,
                                          strategy.meta.timeframe,
                                          product=product)
        print(f"  {strategy.name:14} signals recorded: {n_signals:4d}  "
              f"outcomes labeled: {n_labeled:4d}")

    # 2) identical measurement sweep -> verdicts
    print("\nmeasuring...")
    verdicts = engine.measure_all(strategies, symbols)
    table = engine.league_table(verdicts)

    # 3) render
    print("\n" + "=" * 100)
    print("PHASE 5 LEAGUE TABLE  (" + data_note + ")")
    print("=" * 100)
    columns = ["strategy", "verdict", "n_signals", "trades", "expectancy",
               "win_rate", "profit_factor", "sharpe", "edge_bps",
               "edge_ci_low_bps", "cost_bps", "median_hold_min", "conf_corr"]
    with pd.option_context("display.width", 200, "display.max_columns", 50):
        print(table[[c for c in columns if c in table.columns]]
              .to_string(index=False))
    print("\nVERDICT DETAIL")
    for verdict in verdicts:
        print(f"\n  {verdict.strategy}: {verdict.verdict}")
        for reason in verdict.reasons:
            print(f"    - {reason}")

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
    lines = [f"# Phase 5 league table", "", f"_{data_note}_", "", "```",
             table.to_string(index=False), "```", "", "## Verdict detail", ""]
    for verdict in verdicts:
        lines.append(f"### {verdict.strategy}: {verdict.verdict}")
        lines += [f"- {reason}" for reason in verdict.reasons] + [""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nreport: {REPORT}")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
