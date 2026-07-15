"""CLI wrapper for the validation coordinator.

Run inside the Freqtrade container, e.g.:

    docker compose run --rm --entrypoint python3 freqtrade \
        user_data/scripts/run_validation.py \
        --trades user_data/backtest_results/smoke.json \
        --out user_data/backtest_results/reports/smoke_report.md \
        --config user_data/config.json \
        --title "Smoke Test Report"

The coordinator runs every data-free validation module (metrics, holding
time, portfolio, Monte Carlo, stress) and generates the standardized
18-section report. Regime breakdown activates automatically once daily
OHLCV exists for the traded pairs (post-download phase).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from validation.coordinator import ValidationOptions, run_validation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trades", required=True,
                        help="Freqtrade --export trades file")
    parser.add_argument("--out", required=True, help="report output path (.md)")
    parser.add_argument("--config", default=None, help="config.json path")
    parser.add_argument("--rejections", default=None,
                        help="trade_rejections.jsonl path")
    parser.add_argument("--capital", type=float, default=1000.0)
    parser.add_argument("--max-open-trades", type=int, default=3)
    parser.add_argument("--mc-resamples", type=int, default=10_000)
    parser.add_argument("--title", default="Backtest Validation Report")
    parser.add_argument("--note", default="", help="context note for the report")
    parser.add_argument("--daily-dir", default=None,
                        help="candle dir (e.g. user_data/data/binance) to load "
                             "1d OHLCV for the regime breakdown")
    parser.add_argument("--pairs", nargs="*", default=[],
                        help="pairs to load daily data for (with --daily-dir)")
    args = parser.parse_args()

    daily_ohlcv = None
    if args.daily_dir and args.pairs:
        import pandas as pd
        daily_ohlcv = {}
        for pair in args.pairs:
            feather = (Path(args.daily_dir)
                       / f"{pair.replace('/', '_')}-1d.feather")
            if feather.exists():
                daily_ohlcv[pair] = pd.read_feather(feather)
            else:
                print(f"warning: no daily data for {pair} at {feather}")
        daily_ohlcv = daily_ohlcv or None

    options = ValidationOptions(
        start_capital=args.capital,
        max_open_trades=args.max_open_trades,
        mc_resamples=args.mc_resamples,
        report_title=args.title,
        context_note=args.note,
        command=" ".join(sys.argv),
        config_path=args.config,
        rejections_path=args.rejections,
        daily_ohlcv=daily_ohlcv,
    )
    results = run_validation(args.trades, args.out, options)
    print(f"report: {results['report_path']}")
    print(f"verdict: {results['verdict']['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
