"""Backtest the four retained Phase-15 strategies on the 15m store.

The retired ``orb_15m`` is deliberately absent. Its frozen reports remain
historical evidence, while the canonical ``orb_5m`` requires cross-sectional
and NIFTY context supplied by the production orchestrator and must not be
mislabelled as the old baseline.

Identical capital / brokerage / slippage / risk limits for all five, through the
uniform risk engine (ATR stop, chandelier trail, session square-off). Produces
the comparison table (trades, win rate, PF, net return, CAGR, max DD, Sharpe,
avg win/loss, expectancy, avg hold, consecutive wins/losses, year- and
month-wise returns). No optimisation - this is the measured production baseline.

    .venv/Scripts/python scripts/backtest_intraday.py --symbols-file nifty100.txt
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from algo.core.costs import NseEquityCostModel, Product
from algo.core.logging import configure, get_logger
from algo.data.store import MarketDataStore
from algo.evidence.database import EvidenceDB, MEMORY
from algo.research.engine import ResearchEngine
from algo.research.validation import metrics
from algo.strategies.library import (
    CprBreakout, FirstPullbackAfterBreakout, VwapPullback,
    VwapTrendContinuation,
)

logger = get_logger("scripts.backtest_intraday")
ROOT = Path("user_data")
REPORT = ROOT / "backtest_results" / "reports" / "intraday_production_batch1.md"
CAPITAL = 100_000.0

STRATEGIES = [
    ("vwap_pullback_15m", VwapPullback),
    ("vwap_15m", VwapTrendContinuation),
    ("cpr_breakout_15m", CprBreakout),
    ("first_pullback_15m", FirstPullbackAfterBreakout),
]


def _max_consecutive(mask: np.ndarray) -> int:
    best = cur = 0
    for x in mask:
        cur = cur + 1 if x else 0
        best = max(best, cur)
    return best


def _periodic_returns(trades: pd.DataFrame, fmt: str) -> dict:
    t = trades.copy()
    t["close_date"] = pd.to_datetime(t["close_date"])
    key = t["close_date"].dt.strftime(fmt)
    return {k: round(float(v) / CAPITAL, 4)
            for k, v in t.groupby(key)["profit_abs"].sum().items()}


def analyse(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"trades": 0}
    core = metrics.summarize(trades, CAPITAL)
    hold = metrics.holding_time_stats(trades)
    profit = trades["profit_abs"].to_numpy(float)
    wins, losses = profit[profit > 0], profit[profit < 0]
    ordered = trades.sort_values("close_date")["profit_abs"].to_numpy(float)
    core.update({
        "net_return_pct": round(core["net_profit_abs"] / CAPITAL * 100, 2),
        "avg_win": round(float(wins.mean()), 2) if len(wins) else 0.0,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) else 0.0,
        "avg_hold_min": round(float(hold.get("mean") or 0.0), 1),
        "max_consecutive_wins": _max_consecutive(ordered > 0),
        "yearly": _periodic_returns(trades, "%Y"),
        "monthly": _periodic_returns(trades, "%Y-%m"),
    })
    return core


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols-file", default="nifty100.txt")
    parser.add_argument("--store", default=str(ROOT / "data" / "nse"))
    args = parser.parse_args()
    configure(level=logging.WARNING)

    store = MarketDataStore(args.store)
    have = set(store.symbols("15m"))
    symbols = [s.strip().upper() for s in Path(args.symbols_file).read_text()
               .splitlines() if s.strip() and not s.startswith("#")]
    symbols = [s for s in symbols if s in have]
    print(f"backtesting {len(STRATEGIES)} intraday strategies on {len(symbols)} "
          f"symbols (15m), capital {CAPITAL:,.0f}\n")

    db = EvidenceDB(MEMORY)
    engine = ResearchEngine(db, store=store, cost_model=NseEquityCostModel())

    results = {}
    for name, cls in STRATEGIES:
        trades = engine.simulate_strategy(
            cls(), symbols, product=Product.INTRADAY, persist=False)
        results[name] = analyse(trades)
        r = results[name]
        print(f"  {name:20} trades={r.get('trades',0):6d} "
              f"net={r.get('net_return_pct',0):+7.2f}% "
              f"PF={r.get('profit_factor',0):.2f} -> done")
    db.close()

    cols = ["trades", "win_rate", "profit_factor", "net_return_pct", "cagr",
            "max_drawdown_pct", "sharpe", "sortino", "avg_win", "avg_loss",
            "expectancy", "avg_hold_min", "max_consecutive_wins",
            "max_consecutive_losses"]
    table = pd.DataFrame({n: {c: r.get(c) for c in cols}
                          for n, r in results.items()}).T[cols]

    print("\n" + "=" * 110)
    print("PRODUCTION INTRADAY BATCH 1 - COMPARISON  (15m, NSE intraday costs)")
    print("=" * 110)
    with pd.option_context("display.width", 220, "display.max_columns", 50):
        print(table.to_string())

    lines = ["# Production intraday batch 1 - backtest comparison", "",
             f"_15m store, {len(symbols)} NIFTY-100 symbols, capital "
             f"{CAPITAL:,.0f}, full NSE intraday cost stack, uniform risk-engine "
             "exits. No optimisation._", "", "```", table.to_string(), "```",
             "", "## Year-wise net return (fraction of capital)", ""]
    years = sorted({y for r in results.values() for y in r.get("yearly", {})})
    ydf = pd.DataFrame({n: {y: r.get("yearly", {}).get(y) for y in years}
                        for n, r in results.items()}).T
    lines += ["```", ydf.to_string(), "```"]
    # month-wise: months as rows (43+), strategies as columns
    months = sorted({m for r in results.values() for m in r.get("monthly", {})})
    mdf = pd.DataFrame({n: {m: r.get("monthly", {}).get(m) for m in months}
                        for n, r in results.items()})
    lines += ["", "## Month-wise net return (fraction of capital)", "",
              "```", mdf.to_string(), "```"]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nyear-wise:\n{ydf.to_string()}")
    print(f"\nreport: {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
