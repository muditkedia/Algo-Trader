"""TEMPORARY Phase B data-quality verification (not part of committed tooling).

Checks every downloaded Binance-spot feather for the frozen corpus:
    * file integrity   - exists, readable, non-empty
    * corrupted OHLCV   - NaN rows; high<low; high<max(open,close);
                          low>min(open,close); non-positive price; negative volume
    * duplicate candles - repeated timestamps
    * timestamp continuity / missing candles vs the expected grid
    * gaps              - count + largest contiguous gap (exchange downtime)
    * coverage          - start <= 2019-12-01 and end >= 2026-06-30

Prints a concise table and writes markdown to
user_data/backtest_results/reports/data_quality.md.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DATA_DIR = Path("user_data/data/binance")
PAIRS = ["BTC/USDT", "ETH/USDT"]
TIMEFRAMES = {"1m": "1min", "5m": "5min", "15m": "15min",
              "1h": "1h", "4h": "4h", "1d": "1D"}
CORPUS_START = pd.Timestamp("2019-12-01", tz="UTC")
CORPUS_END = pd.Timestamp("2026-06-30", tz="UTC")
OHLC = ["open", "high", "low", "close"]


def check(pair: str, tf: str, freq: str) -> dict:
    path = DATA_DIR / f"{pair.replace('/', '_')}-{tf}.feather"
    if not path.exists():
        return {"pair": pair, "tf": tf, "status": "MISSING FILE"}
    try:
        df = pd.read_feather(path)
    except Exception as exc:  # file integrity
        return {"pair": pair, "tf": tf, "status": f"UNREADABLE: {exc}"}
    if df.empty:
        return {"pair": pair, "tf": tf, "status": "EMPTY"}

    df = df.sort_values("date").reset_index(drop=True)
    dates = pd.to_datetime(df["date"], utc=True)
    n = len(df)

    dups = int(dates.duplicated().sum())
    nan_rows = int(df[OHLC + ["volume"]].isna().any(axis=1).sum())
    bad = (
        (df["high"] < df["low"])
        | (df["high"] < df[["open", "close"]].max(axis=1))
        | (df["low"] > df[["open", "close"]].min(axis=1))
        | (df[OHLC] <= 0).any(axis=1)
        | (df["volume"] < 0)
    )
    corrupt = int(bad.sum())

    start, end = dates.iloc[0], dates.iloc[-1]
    step = pd.Timedelta(freq)
    expected = int((end - start) / step) + 1
    missing = expected - int(dates.nunique())

    diffs = dates.diff().dropna()
    gap_mask = diffs > step
    n_gaps = int(gap_mask.sum())
    max_gap = str(diffs[gap_mask].max()) if n_gaps else "none"
    non_monotonic = int((diffs <= pd.Timedelta(0)).sum())

    # The corpus end is a Freqtrade timerange boundary: an intraday feed's
    # last candle sits just before 2026-06-30 00:00 (e.g. 1m ends 23:59 on
    # 06-29), so allow up to one day of slack at the end.
    coverage_ok = bool(start <= CORPUS_START
                       and end >= CORPUS_END - pd.Timedelta(days=1))
    file_mb = round(path.stat().st_size / 1024 / 1024, 2)
    clean = (dups == 0 and corrupt == 0 and nan_rows == 0
             and non_monotonic == 0 and coverage_ok)
    return {
        "pair": pair, "tf": tf, "status": "OK" if clean else "REVIEW",
        "candles": n, "start": str(start), "end": str(end),
        "missing": missing, "gaps": n_gaps, "max_gap": max_gap,
        "duplicates": dups, "corrupt": corrupt, "nan_rows": nan_rows,
        "non_monotonic": non_monotonic, "coverage_ok": coverage_ok,
        "file_mb": file_mb,
    }


def main() -> int:
    rows = [check(pair, tf, freq)
            for pair in PAIRS for tf, freq in TIMEFRAMES.items()]

    header = (f"{'pair':<9}{'tf':<5}{'status':<8}{'candles':>10}"
              f"{'missing':>9}{'gaps':>6}{'dups':>6}{'corrupt':>9}"
              f"{'cover':>7}{'MB':>9}")
    print("=" * len(header))
    print("Phase B - Data Quality Report")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    problems = 0
    for r in rows:
        if r["status"] not in ("OK", "REVIEW"):
            print(f"{r['pair']:<9}{r['tf']:<5}{r['status']}")
            problems += 1
            continue
        print(f"{r['pair']:<9}{r['tf']:<5}{r['status']:<8}{r['candles']:>10,}"
              f"{r['missing']:>9,}{r['gaps']:>6}{r['duplicates']:>6}"
              f"{r['corrupt']:>9}{str(r['coverage_ok']):>7}{r['file_mb']:>9}")
        if r["status"] == "REVIEW":
            problems += 1

    total_mb = round(sum(r.get("file_mb", 0) for r in rows), 2)
    print("-" * len(header))
    print(f"total dataset size: {total_mb} MB across {len(rows)} files")
    print(f"files needing review: {problems}")

    # detail on any gaps (exchange downtime is expected & benign)
    print("\nGap / coverage detail:")
    for r in rows:
        if r.get("gaps") or not r.get("coverage_ok", True):
            print(f"  {r['pair']} {r['tf']}: gaps={r.get('gaps')} "
                  f"max_gap={r.get('max_gap')} missing={r.get('missing')} "
                  f"coverage_ok={r.get('coverage_ok')} "
                  f"range={r.get('start')} -> {r.get('end')}")

    _write_markdown(rows, total_mb, problems)
    # exit non-zero only for hard failures (unreadable/missing/corrupt/dup)
    hard = any(r["status"] not in ("OK", "REVIEW") or r.get("duplicates")
               or r.get("corrupt") or r.get("nan_rows")
               or r.get("non_monotonic") for r in rows)
    print(f"\nRESULT: {'HARD ISSUES FOUND' if hard else 'NO HARD ISSUES'}")
    return 1 if hard else 0


def _write_markdown(rows: list, total_mb: float, problems: int) -> None:
    out = Path("user_data/backtest_results/reports/data_quality.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Phase B - Data Quality Report", "",
        f"_Generated {datetime.now(timezone.utc).isoformat()}._", "",
        f"Corpus target: {CORPUS_START.date()} -> {CORPUS_END.date()} "
        "(Binance spot).", "",
        f"Total size: **{total_mb} MB** across {len(rows)} files; "
        f"files needing review: **{problems}**.", "",
        "| Pair | TF | Status | Candles | Missing | Gaps | Dups | Corrupt "
        "| NaN | Coverage | MB |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r["status"] in ("OK", "REVIEW"):
            lines.append(
                f"| {r['pair']} | {r['tf']} | {r['status']} | {r['candles']:,} "
                f"| {r['missing']:,} | {r['gaps']} | {r['duplicates']} "
                f"| {r['corrupt']} | {r['nan_rows']} | {r['coverage_ok']} "
                f"| {r['file_mb']} |")
        else:
            lines.append(f"| {r['pair']} | {r['tf']} | {r['status']} "
                         "| - | - | - | - | - | - | - | - |")
    lines += ["", "## Coverage / gap detail", ""]
    for r in rows:
        if r.get("gaps") or not r.get("coverage_ok", True):
            lines.append(
                f"- **{r['pair']} {r['tf']}**: {r.get('gaps')} gap(s), "
                f"largest {r.get('max_gap')}, {r.get('missing')} missing "
                f"candles; range {r.get('start')} -> {r.get('end')}.")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nmarkdown report: {out}")


if __name__ == "__main__":
    sys.exit(main())
