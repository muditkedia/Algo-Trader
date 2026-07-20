"""Download NSE historical candles from SmartAPI into the MarketDataStore.

    .venv/Scripts/python scripts/download_history.py --symbols RELIANCE,TCS
    .venv/Scripts/python scripts/download_history.py --symbols-file nifty50.txt

Incremental and resumable BY CONSTRUCTION: the window is computed from the
store's per-symbol coverage (IngestionEngine) and the store upserts on
timestamp - so interrupting and re-running the same command simply continues
where it stopped, downloading nothing twice. Fetched data passes the quality
gates before being admitted; failures are quarantined and reported.

Defaults: timeframes 15m,1h,1d into user_data/data/nse, history from
--start (default 2023-01-01) to --end (default now).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from algo.core.logging import configure
from algo.data import manifest
from algo.data.ingest import IngestionEngine
from algo.data.providers.smartapi import SmartApiConfig, build_provider
from algo.data.providers.smartapi.instruments import INDEX_SYMBOLS
from algo.data.store import MarketDataStore

DEFAULT_STORE = "user_data/data/nse"
DEFAULT_TIMEFRAMES = "15m,1h,1d"


def parse_symbols(args) -> list:
    if args.symbols:
        return [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.symbols_file:
        text = Path(args.symbols_file).read_text(encoding="utf-8")
        return [line.strip().upper() for line in text.splitlines()
                if line.strip() and not line.startswith("#")]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", help="comma-separated NSE symbols "
                        "(e.g. RELIANCE,TCS,INFY)")
    parser.add_argument("--symbols-file", help="file with one symbol per line")
    parser.add_argument("--context", action="store_true",
                        help="include the market-context series "
                             f"({','.join(INDEX_SYMBOLS)}); may be used "
                             "alone or with --symbols/--symbols-file")
    parser.add_argument("--timeframes", default=DEFAULT_TIMEFRAMES)
    parser.add_argument("--start", default="2023-01-01",
                        help="history start when a symbol has no data yet")
    parser.add_argument("--end", default=None, help="default: now")
    parser.add_argument("--store", default=DEFAULT_STORE)
    args = parser.parse_args()
    configure(level=logging.INFO)

    symbols = parse_symbols(args)
    if args.context:
        symbols += [s for s in INDEX_SYMBOLS if s not in symbols]
    if not symbols:
        print("no symbols given - use --symbols, --symbols-file or --context")
        return 2

    config = SmartApiConfig.from_env()
    missing = config.missing()
    if missing:
        print("missing SmartAPI credentials:", ", ".join(missing))
        print("create .env from .env.example first "
              "(scripts/smartapi_login_check.py verifies it).")
        return 2

    provider = build_provider(cache_dir=Path(args.store) / "_instruments")
    provider.instruments.ensure()
    if args.context:
        resolved = provider.instruments.ensure_indices()
        absent = sorted(set(INDEX_SYMBOLS) - set(resolved["symbol"]))
        if absent:
            print(f"WARNING: provider does not list these indices: {absent} "
                  "- they will be skipped (documented, not fatal)")
            symbols = [s for s in symbols if s not in absent]
    unknown = [s for s in symbols if provider.instruments.token_for(s) is None]
    if unknown:
        print(f"WARNING: not in the NSE instrument master: {unknown}")
    store = MarketDataStore(args.store)
    engine = IngestionEngine(store, provider)

    exit_code = 0
    for timeframe in [t.strip() for t in args.timeframes.split(",") if t.strip()]:
        print(f"\n=== {timeframe}: {len(symbols)} symbols ===")
        report = engine.incremental_update(
            symbols, timeframe, end=args.end, start_if_empty=args.start)
        manifest.record(store, report, provider.name)
        summary = report.summary()
        print(f"    {summary}")
        for result in report.results:
            if result.status == "quarantined":
                exit_code = 1
                print(f"    QUARANTINED {result.symbol}: {result.detail}")
            coverage = store.coverage(result.symbol, timeframe)
            if coverage:
                print(f"    {result.symbol:12} {result.status:11} "
                      f"rows={coverage['rows']:6d}  "
                      f"{coverage['start']:%Y-%m-%d} -> {coverage['end']:%Y-%m-%d}")
    provider.session.logout()
    print("\ndownload complete (re-run the same command to resume/update).")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
