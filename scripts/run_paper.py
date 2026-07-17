"""Start paper trading (no live orders - ever).

    .venv/Scripts/python scripts/run_paper.py --symbols-file my_symbols.txt --loop

Prerequisites (in order):
  1. scripts/smartapi_login_check.py       - credentials verified
  2. scripts/download_history.py           - store populated
  3. scripts/run_measurement.py --store-dir user_data/data/nse
                                           - strategies measured; at least one
                                             must survive (status "measured"+)

The engine refuses strategies that have not survived measurement on real data
(--allow-unmeasured is for offline pipeline validation only).

Continuous operation (--loop): a short TICK (default 15s) manages open
positions on every pass, while the adaptive scheduler decides which strategy
timeframes to re-scan (15m ~45s, 1h ~3min, 1d ~7min - configurable). Fresh
bars are pulled from SmartAPI before due scans unless --no-refresh. The
console dashboard re-renders every tick; state resumes across restarts.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from algo.core.logging import configure
from algo.data.ingest import IngestionEngine
from algo.data.store import MarketDataStore
from algo.evidence.database import EvidenceDB
from algo.evidence.logger import EvidenceLogger
from algo.paper import dashboard
from algo.paper.engine import PaperEngine
from algo.paper.portfolio import PortfolioConfig
from algo.paper.sizing import SizingConfig
from algo.scanner.ranking import RankingWeights
from algo.scanner.scheduler import ScanCadence
from algo.strategies.library import ALL_STRATEGIES

DEFAULT_STORE = "user_data/data/nse"
DEFAULT_DB = "user_data/evidence/evidence.db"


def parse_symbols(args) -> list:
    if args.symbols:
        return [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.symbols_file:
        return [line.strip().upper() for line in
                Path(args.symbols_file).read_text().splitlines()
                if line.strip() and not line.startswith("#")]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", help="comma-separated NSE symbols")
    parser.add_argument("--symbols-file")
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--tick", type=int, default=15,
                        help="seconds between management ticks in --loop mode")
    parser.add_argument("--loop", action="store_true",
                        help="run continuously (default: one cycle)")
    parser.add_argument("--no-refresh", action="store_true",
                        help="do not pull fresh bars from SmartAPI")
    parser.add_argument("--capital", type=float, default=1_000_000.0)
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--stake-cap", type=float, default=100_000.0,
                        help="per-trade stake CAP (sizing is dynamic)")
    parser.add_argument("--min-confidence", type=float, default=0.5)
    parser.add_argument("--allow-unmeasured", action="store_true",
                        help="OFFLINE VALIDATION ONLY")
    parser.add_argument("--no-dashboard", action="store_true")
    args = parser.parse_args()
    configure(level=logging.INFO, logfile="user_data/logs/paper.log")
    logging.getLogger("algo").handlers = [
        h for h in logging.getLogger("algo").handlers
        if not isinstance(h, logging.StreamHandler)
        or isinstance(h, logging.FileHandler)]     # dashboard owns the console

    symbols = parse_symbols(args)
    if not symbols:
        print("no symbols given - use --symbols or --symbols-file")
        return 2

    store = MarketDataStore(args.store)
    db = EvidenceDB(args.db)
    log = EvidenceLogger(db)
    for symbol in symbols:
        log.upsert_instrument(symbol)

    ingestion = None
    if not args.no_refresh:
        from algo.data.providers.smartapi import SmartApiConfig, build_provider
        config = SmartApiConfig.from_env()
        if config.missing():
            print("missing SmartAPI credentials:", ", ".join(config.missing()))
            print("use --no-refresh to paper-trade on already-stored data.")
            return 2
        provider = build_provider(cache_dir=Path(args.store) / "_instruments")
        provider.instruments.ensure()
        ingestion = IngestionEngine(store, provider)

    try:
        engine = PaperEngine(
            store, [cls() for cls in ALL_STRATEGIES], log,
            portfolio=PortfolioConfig(total_capital=args.capital,
                                      max_open_positions=args.max_positions),
            sizing=SizingConfig(max_stake=args.stake_cap),
            ranking_weights=RankingWeights(), cadence=ScanCadence(),
            min_confidence=args.min_confidence,
            allow_unmeasured=args.allow_unmeasured)
    except RuntimeError as exc:
        print(f"REFUSED: {exc}")
        return 3
    print(f"paper engine up: strategies={[s.name for s in engine.strategies]}"
          f", symbols={len(symbols)}, tick={args.tick}s")

    while True:
        due = engine.scheduler.due()
        if ingestion is not None and due:
            for timeframe in due:
                ingestion.incremental_update(symbols, timeframe,
                                             default_lookback_days=10)
        engine.cycle(symbols=symbols)
        if not args.no_dashboard:
            print(dashboard.render(
                engine.snapshot(), universe_size=len(symbols),
                eligible=len(symbols),
                health={"data": store.timeframes() != [],
                        "evidence": True,
                        "session": ingestion is not None or args.no_refresh}))
        if not args.loop:
            break
        time.sleep(max(1, args.tick))
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
