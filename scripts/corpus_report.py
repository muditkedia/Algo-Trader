"""Report the research corpus's coverage (Phase 11, Part A).

Reads the market-data store and the NIFTY-500 snapshot and reports, for a given
timeframe: total symbols, oldest date, history-length distribution, symbols with
short/incomplete history, likely-new listings, and a rough estimate of the
independent-sample gain versus the prior 3.5-year corpus. Read-only.

    .venv/Scripts/python scripts/corpus_report.py [--timeframe 1d]
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import pandas as pd

from algo.data.store import MarketDataStore

ROOT = Path("user_data")
NIFTY500_CSV = ROOT / "universe" / "ind_nifty500_2026-07.csv"
#: Below this many daily bars a symbol is too short for a 12-month-formation,
#: multi-week-horizon study (needs formation + horizon + slack).
SHORT_BARS = 400


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeframe", default="1d")
    parser.add_argument("--store", default=str(ROOT / "data" / "nse"))
    args = parser.parse_args()

    store = MarketDataStore(args.store)
    tf = args.timeframe
    target = {row["Symbol"].strip() for row in
              csv.DictReader(open(NIFTY500_CSV, encoding="utf-8-sig"))
              if row.get("Series", "").strip() == "EQ"} \
        if NIFTY500_CSV.exists() else set()

    rows = []
    for sym in store.symbols(tf):
        cov = store.coverage(sym, tf)
        if cov:
            rows.append({"symbol": sym, "rows": cov["rows"],
                         "start": cov["start"], "end": cov["end"]})
    frame = pd.DataFrame(rows)
    if frame.empty:
        print("no data")
        return 1
    frame["years"] = frame["rows"] / 252.0

    have = set(frame["symbol"])
    excluded = sorted(target - have) if target else []
    short = frame[frame["rows"] < SHORT_BARS].sort_values("rows")
    modal_start = frame["start"].mode()[0]
    newly_listed = frame[frame["start"] > modal_start + pd.Timedelta(days=90)]

    print("=" * 70)
    print(f"CORPUS REPORT  ({tf})   store={args.store}")
    print("=" * 70)
    print(f"total symbols in store        : {len(frame)}")
    if target:
        print(f"NIFTY-500 target              : {len(target)}")
        print(f"NIFTY-500 present             : {len(have & target)}")
        print(f"NIFTY-500 excluded (no data)  : {len(excluded)}"
              + (f" - {excluded[:10]}..." if excluded else ""))
    print(f"oldest available date         : {frame['start'].min():%Y-%m-%d}")
    print(f"newest date                   : {frame['end'].max():%Y-%m-%d}")
    print(f"history length (bars)  mean   : {frame['rows'].mean():.0f}")
    print(f"                       median : {frame['rows'].median():.0f}")
    print(f"                       min/max : {frame['rows'].min()}/{frame['rows'].max()}")
    print(f"history length (years) mean   : {frame['years'].mean():.1f}")
    print(f"                       median : {frame['years'].median():.1f}")
    print(f"symbols with < {SHORT_BARS} bars (short): {len(short)}"
          + (f" - {list(short['symbol'][:10])}" if len(short) else ""))
    print(f"likely newly-listed (start > modal+90d): {len(newly_listed)}")

    # rough independent-sample estimate: ~1 independent block per year of the
    # median symbol, times the count of symbols with usable history, vs the
    # prior corpus (3.5y x 99 symbols).
    usable = frame[frame["rows"] >= SHORT_BARS]
    med_years = usable["years"].median()
    prior_blocks_20d = (3.5 * 252 / 20)          # ~44 non-overlapping 20d/symbol
    now_blocks_20d = (med_years * 252 / 20)
    print(f"\nindependent 20-day blocks / symbol  prior ~{prior_blocks_20d:.0f}  "
          f"now ~{now_blocks_20d:.0f}")
    print(f"usable symbols (>= {SHORT_BARS} bars)  prior 99  now {len(usable)}")
    print(f"=> cross-sectional breadth x{len(usable)/99:.1f}, "
          f"time-depth x{med_years/3.5:.1f} (independent PERIODS, the scarcer "
          "axis, up ~{:.1f}x)".format(med_years / 3.5))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
