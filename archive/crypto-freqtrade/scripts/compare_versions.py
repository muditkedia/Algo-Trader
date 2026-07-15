"""TEMPORARY 3-way comparison: AdaptiveTrend v1 vs v2 vs v3 (read-only).

For each backtest export computes the metrics the Phase-E/v3 task requires:
  * exit contribution to PnL, split into trailing / structural / initial stop
  * MFE capture (winners) and give-back
  * average exit lag from the trade's peak (5m candle reconstruction)
  * average + median holding time
  * fees paid
  * headline performance (win rate, net, PF, expectancy, Sharpe-ish inputs)

Usage: compare_versions.py <v1.zip> <v2.zip> <v3.zip>
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append("user_data/scripts")
from validation import loaders, metrics as M

DATA = Path("user_data/data/binance")
PAIRS = ["BTC/USDT", "ETH/USDT"]
CAPITAL = 1000.0


def categorize(reason: str) -> str:
    r = (reason or "").lower()
    if "trailing" in r:
        return "trailing"
    if r == "stop_loss":
        return "initial_stop"
    if any(k in r for k in ("reversal", "exhaustion", "breakdown", "exit_")):
        return "structural"
    return "other"


def peak_lag(trades: pd.DataFrame) -> pd.Series:
    """Minutes between the trade's peak (5m high) and its exit."""
    frames = {}
    for p in PAIRS:
        f = pd.read_feather(DATA / f"{p.replace('/', '_')}-5m.feather")
        f["date"] = pd.to_datetime(f["date"], utc=True)
        frames[p] = f.set_index("date")["high"]
    lags = []
    for t in trades.itertuples():
        seg = frames[t.pair].loc[t.open_date:t.close_date]
        if len(seg):
            lags.append((t.close_date - seg.idxmax()).total_seconds() / 60.0)
        else:
            lags.append(np.nan)
    return pd.Series(lags, index=trades.index)


def analyse(label: str, zip_path: str) -> dict:
    t = loaders.load_backtest_export(zip_path)
    t["mfe"] = (t["max_rate"] - t["open_rate"]) / t["open_rate"]
    t["give_back"] = t["mfe"] - t["profit_ratio"]
    t["category"] = t["exit_reason"].map(categorize)
    t["lag"] = peak_lag(t)
    fees = float((t["amount"] * t["open_rate"] * t["fee_open"]
                  + t["amount"] * t["close_rate"] * t["fee_close"]).sum())
    win = t["profit_abs"] > 0
    summary = M.summarize(t, CAPITAL)
    return {
        "label": label, "trades": len(t), "win": float(win.mean()),
        "net": float(t["profit_abs"].sum()), "fees": fees,
        "pf": summary["profit_factor"], "expectancy": summary["expectancy"],
        "sharpe": summary["sharpe"], "sortino": summary["sortino"],
        "maxdd": summary["max_drawdown_pct"],
        "mfe": float(t["mfe"].mean()),
        "give_back": float(t["give_back"].mean()),
        "capture": float(t.loc[win, "profit_ratio"].mean()
                         / t.loc[win, "mfe"].mean()),
        "lag": float(t["lag"].mean()),
        "hold_mean": float(t["trade_duration"].mean()),
        "hold_med": float(t["trade_duration"].median()),
        "frame": t,
    }


def main() -> int:
    paths = sys.argv[1:4]
    labels = ["v1", "v2", "v3"]
    results = [analyse(l, p) for l, p in zip(labels, paths)]

    print("=" * 100)
    print("HEADLINE (dev corpus 2020-01..2025-06, BTC+ETH, 0.1%/side fees)")
    print("=" * 100)
    hdr = (f"{'':4}{'trades':>7}{'win':>7}{'net':>9}{'fees':>8}{'PF':>6}"
           f"{'expect':>9}{'Sharpe':>8}{'Sortino':>8}{'maxDD':>7}")
    print(hdr)
    for r in results:
        print(f"{r['label']:4}{r['trades']:>7}{r['win']:>6.1%}{r['net']:>+9.1f}"
              f"{r['fees']:>8.1f}{r['pf']:>6.2f}{r['expectancy']:>+9.4f}"
              f"{r['sharpe']:>8.2f}{r['sortino']:>8.2f}{r['maxdd']:>6.1%}")

    print("\n" + "=" * 100)
    print("EXIT QUALITY")
    print("=" * 100)
    print(f"{'':4}{'mean MFE':>10}{'give-back':>11}{'winner capture':>16}"
          f"{'exit lag(min)':>15}{'hold mean':>11}{'hold med':>10}")
    for r in results:
        print(f"{r['label']:4}{r['mfe']:>+10.4f}{r['give_back']:>+11.4f}"
              f"{r['capture']:>15.0%}{r['lag']:>15.0f}"
              f"{r['hold_mean']:>11.0f}{r['hold_med']:>10.0f}")

    print("\n" + "=" * 100)
    print("EXIT CONTRIBUTION TO PnL (USDT)")
    print("=" * 100)
    cats = ["trailing", "structural", "initial_stop", "other"]
    print(f"{'':4}" + "".join(f"{c:>24}" for c in cats))
    for r in results:
        cells = []
        for c in cats:
            g = r["frame"][r["frame"]["category"] == c]
            if len(g):
                cells.append(f"{g['profit_abs'].sum():+8.1f} (n={len(g):4}, "
                             f"{ (g['profit_abs']>0).mean():.0%}w)")
            else:
                cells.append("-")
        print(f"{r['label']:4}" + "".join(f"{c:>24}" for c in cells))

    print("\n" + "=" * 100)
    print("STRUCTURAL EXIT DETAIL (the single variable under test)")
    print("=" * 100)
    for r in results:
        g = r["frame"][r["frame"]["category"] == "structural"]
        if not len(g):
            print(f"  {r['label']}: none")
            continue
        print(f"  {r['label']}: n={len(g):5} net={g['profit_abs'].sum():+8.1f} "
              f"win={(g['profit_abs']>0).mean():5.1%} "
              f"mfe={g['mfe'].mean():+.4f} realized={g['profit_ratio'].mean():+.4f} "
              f"lag={g['lag'].mean():5.0f}min hold={g['trade_duration'].median():5.0f}min "
              f"reasons={sorted(g['exit_reason'].unique())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
