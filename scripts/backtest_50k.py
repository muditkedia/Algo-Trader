"""Standardized backtest: every implemented intraday strategy at Rs 50,000/trade.

Project-reset Phases 2-3. One configuration for all strategies: fixed
Rs 50,000 notional per trade (no sizing, no optimisation, fractional shares),
the full NSE intraday cost stack, the 15m/1h NIFTY-100 store (~3.5y - the
largest reliable intraday dataset; NIFTY-500 exists only at daily resolution).

Engines:
  * ``owned``  (default): the strategy-owned execution engine
    (algo.execution) - each strategy's declared entry timing / stop / target /
    trail / session rules, with the three confirmed bugs fixed (no last-bar
    overnight carry, no generic 8-bar cap, honest gap fills).
  * ``legacy``: the pre-reset research simulator path, kept verbatim so the
    recorded retained baselines stay reproducible. Retired ``orb_15m`` and
    ``first_pullback_15m`` implementations are intentionally not aliased to
    canonical STRAT-01/02; their frozen reports remain legacy evidence.

--compare runs BOTH on identical signals and writes the correctness report:
per-strategy metrics, per-fix trade-change attribution, and validation
assertions (zero overnight carries; every square-off on the session's actual
last bar).

    .venv/Scripts/python scripts/backtest_50k.py --compare
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from algo.core.costs import NseEquityCostModel, Product
from algo.core.indicators import atr as atr_series
from algo.core.logging import configure
from algo.data.store import MarketDataStore
from algo.evidence.database import EvidenceDB, MEMORY
from algo.execution import execute_signal
from algo.research.engine import ResearchEngine
from algo.research.simulator import simulate_trade
from algo.research.validation import metrics
from algo.strategies.library import (
    CprBreakout,
    PullbackContinuation15m, VolatilityExpansionBreakout1h, VwapPullback,
    VwapTrendContinuation,
)

ROOT = Path("user_data")
REPORT = ROOT / "backtest_results" / "reports" / "intraday_50k_backtest_fixed.md"
LEGACY_REPORT = ROOT / "backtest_results" / "reports" / "intraday_50k_backtest.md"
STAKE = 50_000.0

#: Every implemented intraday strategy in the library (holding_scope=INTRADAY).
STRATEGIES = [
    ("vwap_15m", VwapTrendContinuation),
    ("vwap_pullback_15m", VwapPullback),
    ("cpr_breakout_15m", CprBreakout),
    ("pullback_15m", PullbackContinuation15m),
    ("volexp_1h", VolatilityExpansionBreakout1h),
]

TRADE_COLUMNS = ["pair", "open_date", "close_date", "profit_ratio",
                 "profit_abs", "stake_amount", "trade_duration", "exit_reason",
                 "gross_ratio", "cost_ratio", "gap_fill", "partial"]


def _work_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    work = frame.copy()
    work["symbol"] = symbol
    if "atr" not in work.columns:
        work["atr"] = atr_series(work, 14)
    work["swing_low_calc"] = work["low"].rolling(10, min_periods=1).min()
    return work


def _legacy_row(t) -> dict:
    return {"pair": t.symbol, "open_date": t.open_date,
            "close_date": t.close_date, "profit_ratio": t.profit_ratio,
            "profit_abs": t.profit_abs, "stake_amount": t.stake_amount,
            "trade_duration": t.holding_min, "exit_reason": t.exit_reason,
            "gross_ratio": t.gross_ratio, "cost_ratio": t.cost_ratio,
            "gap_fill": False, "partial": False}


def _owned_row(t) -> dict:
    row = _legacy_row(t)
    row["gap_fill"] = t.gap_fill
    row["partial"] = t.partial
    return row


def run_both(engine: ResearchEngine, strategy, symbols, stake: float,
             which: str = "both"):
    """Run legacy and/or owned execution on IDENTICAL prepared signals.

    Returns (legacy_frame, owned_frame, audit) where audit carries the
    per-fix attribution counts computed against each symbol's session map.
    """
    prepared = engine.prepare_signals(strategy, symbols)
    max_bars = int(strategy.meta.max_hold_bars)
    spec = strategy.execution
    legacy_rows, owned_rows = [], []
    audit = {"signals": 0, "fix1_lastbar_entries_dropped": 0,
             "fix2_capped_squareoffs_extended": 0, "fix3_gap_fill_stops": 0,
             "owned_skipped_no_stop": 0, "squareoffs_not_at_session_end": 0,
             "owned_overnight": 0}

    for symbol in sorted(prepared.frames):
        work = _work_frame(prepared.frames[symbol], symbol)
        idx = np.flatnonzero(prepared.signals[symbol].to_numpy(bool))
        audit["signals"] += len(idx)
        day = work["date"].dt.normalize()
        last_bar_of_day = work.groupby(day)["date"].transform("max")

        for i in idx:
            i = int(i)
            atr_i = float(work["atr"].iloc[i])
            swing_i = float(work["swing_low_calc"].iloc[i])
            is_last_bar = work["date"].iloc[i] == last_bar_of_day.iloc[i]

            if which in ("both", "legacy"):
                old = simulate_trade(
                    work, i, atr=atr_i, swing_low=swing_i,
                    params=engine.risk_params, cost_model=engine.cost_model,
                    product=Product.INTRADAY, stake=stake, max_bars=max_bars)
                if old is not None:
                    legacy_rows.append(_legacy_row(old))
                    if is_last_bar:
                        audit["fix1_lastbar_entries_dropped"] += 1
                    elif (old.exit_reason == "session_squareoff"
                          and pd.Timestamp(old.close_date)
                          != last_bar_of_day.iloc[i]):
                        audit["fix2_capped_squareoffs_extended"] += 1

            if which in ("both", "owned"):
                new = execute_signal(
                    work, i, spec=spec, atr=atr_i, swing_low=swing_i,
                    cost_model=engine.cost_model, product=Product.INTRADAY,
                    stake=stake, params=engine.risk_params)
                if new is None:
                    if not is_last_bar and i < len(work) - 1:
                        audit["owned_skipped_no_stop"] += 1
                    continue
                owned_rows.append(_owned_row(new))
                if new.gap_fill and new.exit_reason in (
                        "stop_loss", "trailing_stop", "breakeven_stop"):
                    audit["fix3_gap_fill_stops"] += 1
                # validation: same-day always; square-offs at the true last bar
                if (pd.Timestamp(new.close_date).normalize()
                        != pd.Timestamp(new.open_date).normalize()):
                    audit["owned_overnight"] += 1
                if (new.exit_reason == "session_squareoff"
                        and pd.Timestamp(new.close_date)
                        != last_bar_of_day.iloc[i]):
                    audit["squareoffs_not_at_session_end"] += 1

    legacy = pd.DataFrame(legacy_rows, columns=TRADE_COLUMNS)
    owned = pd.DataFrame(owned_rows, columns=TRADE_COLUMNS)
    return legacy, owned, audit


def analyse(trades: pd.DataFrame, stake: float) -> dict:
    """Rupee-term report battery for one strategy (fixed stake per trade)."""
    if trades.empty:
        return {"trades": 0}
    core = metrics.summarize(trades, stake)          # sharpe/sortino/cagr/dd
    profit = trades["profit_abs"].to_numpy(float)
    wins, losses = profit[profit > 0], profit[profit < 0]
    years = max((pd.Timestamp(trades["close_date"].max())
                 - pd.Timestamp(trades["open_date"].min())).days / 365.25, 1e-9)
    net = float(profit.sum())
    overnight = (pd.to_datetime(trades["close_date"]).dt.normalize()
                 != pd.to_datetime(trades["open_date"]).dt.normalize())
    return {
        "trades": int(len(trades)),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate_pct": round(100.0 * len(wins) / len(trades), 2),
        "avg_win_rs": round(float(wins.mean()), 2) if len(wins) else 0.0,
        "avg_loss_rs": round(float(losses.mean()), 2) if len(losses) else 0.0,
        "net_profit_rs": round(net, 2),
        "profit_factor": round(core.get("profit_factor", 0.0), 4),
        "max_drawdown_rs": round(core.get("max_drawdown_abs", 0.0), 2),
        "total_return_pct": round(100.0 * net / stake, 2),
        "avg_return_per_trade_rs": round(net / len(trades), 2),
        "avg_return_per_trade_bps": round(
            10_000.0 * float(trades["profit_ratio"].mean()), 2),
        "final_capital_rs": round(stake + net, 2),
        "annualized_simple_return_pct": round(100.0 * net / stake / years, 2),
        "sharpe": core.get("sharpe"),
        "avg_cost_bps": round(
            10_000.0 * float(trades["cost_ratio"].mean()), 2),
        "avg_hold_min": round(float(trades["trade_duration"].mean()), 1),
        "overnight_carry_trades": int(overnight.sum()),
        "partials": int(trades["partial"].sum()),
        "exit_reasons": trades["exit_reason"].value_counts().to_dict(),
        "start": str(pd.Timestamp(trades["open_date"].min()).date()),
        "end": str(pd.Timestamp(trades["close_date"].max()).date()),
    }


ROW_LABELS = [
    ("trades", "Number of trades"),
    ("wins", "Winning trades"),
    ("losses", "Losing trades"),
    ("win_rate_pct", "Win rate (%)"),
    ("avg_win_rs", "Avg winning trade (Rs)"),
    ("avg_loss_rs", "Avg losing trade (Rs)"),
    ("net_profit_rs", "Net profit (Rs)"),
    ("profit_factor", "Profit factor"),
    ("max_drawdown_rs", "Max drawdown (Rs, equity curve)"),
    ("avg_return_per_trade_rs", "Avg return per trade (Rs)"),
    ("avg_return_per_trade_bps", "Avg return per trade (bps)"),
    ("final_capital_rs", "Final capital (Rs 50k start)"),
    ("total_return_pct", "Total return (% of Rs 50k)"),
    ("annualized_simple_return_pct", "Annualized simple return (%/yr)"),
    ("sharpe", "Sharpe (annualized, daily P&L)"),
    ("avg_cost_bps", "Avg round-trip cost (bps)"),
    ("avg_hold_min", "Avg holding time (min)"),
    ("partials", "Trades with a partial exit"),
    ("overnight_carry_trades", "Overnight-carry trades"),
]


def _metric_table(results: dict, order) -> list:
    header = "| Metric | " + " | ".join(order) + " |"
    sep = "|---" * (len(order) + 1) + "|"
    lines = [header, sep]
    for key, label in ROW_LABELS:
        row = [label]
        for name in order:
            v = results[name].get(key)
            row.append(f"{v:,.2f}" if isinstance(v, float) else str(v))
        lines.append("| " + " | ".join(row) + " |")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols-file", default="nifty100.txt")
    parser.add_argument("--store", default=str(ROOT / "data" / "nse"))
    parser.add_argument("--engine", choices=["owned", "legacy"],
                        default="owned")
    parser.add_argument("--compare", action="store_true",
                        help="run BOTH engines and write the correctness "
                             "comparison report")
    args = parser.parse_args()
    configure(level=logging.WARNING)

    store = MarketDataStore(args.store)
    listed = [s.strip().upper() for s in Path(args.symbols_file).read_text()
              .splitlines() if s.strip() and not s.startswith("#")]
    db = EvidenceDB(MEMORY)
    engine = ResearchEngine(db, store=store, cost_model=NseEquityCostModel())

    which = "both" if args.compare else args.engine
    legacy_res, owned_res, audits = {}, {}, {}
    for name, cls in STRATEGIES:
        strategy = cls()
        have = set(store.symbols(strategy.meta.timeframe))
        symbols = [s for s in listed if s in have]
        legacy, owned, audit = run_both(engine, strategy, symbols, STAKE,
                                        which=which)
        audits[name] = audit
        if not legacy.empty or which == "legacy":
            legacy_res[name] = analyse(legacy, STAKE)
        if not owned.empty or which == "owned":
            owned_res[name] = analyse(owned, STAKE)
        r = owned_res.get(name) or legacy_res.get(name) or {}
        print(f"  {name:20} signals={audit['signals']:6d} "
              f"trades={r.get('trades', 0):6d} "
              f"net=Rs {r.get('net_profit_rs', 0):+14,.0f} "
              f"PF={r.get('profit_factor', 0):.3f}")
    db.close()

    results = owned_res if which in ("both", "owned") else legacy_res
    ranked = sorted(results, key=lambda n: results[n].get("net_profit_rs", 0.0),
                    reverse=True)

    lines = [
        "# Intraday backtest at Rs 50,000/trade - strategy-owned execution "
        "(corrected)", "",
        "_Project-reset Phases 2-3. Same seven strategies, same dataset (99",
        "NIFTY-100 symbols, 15m/1h bars, 2023-01-02 to 2026-07-17), same",
        "capital (Rs 50,000 fixed per trade), same costs (full NSE intraday",
        "stack), same slippage (2 bps/side), same universe as the pre-fix run",
        "(`intraday_50k_backtest.md`). Only execution correctness changed:",
        "every strategy now OWNS its declared entry timing, stop, target,",
        "trailing, session restrictions and square-off, and the engine",
        "(algo/execution) interprets that declaration._", "",
    ]

    if which in ("both", "owned"):
        lines += ["## Standardized report - corrected (strategy-owned "
                  "execution)", ""]
        lines += _metric_table(owned_res, ranked)
        lines += ["", "### Exit-reason breakdown (corrected run)", ""]
        for name in ranked:
            lines.append(f"- **{name}**: {owned_res[name].get('exit_reasons')}")
        lines += ["", "### Validation (asserted on every trade)", ""]
        total_overnight = sum(a["owned_overnight"] for a in audits.values())
        total_bad_sq = sum(a["squareoffs_not_at_session_end"]
                           for a in audits.values())
        lines += [
            f"- Overnight-carry trades in the corrected run: "
            f"**{total_overnight}** (required: 0)",
            f"- Square-offs not on the session's actual last bar: "
            f"**{total_bad_sq}** (required: 0)",
        ]

    if which == "both":
        lines += ["", "## Implementation bugs fixed, and how many trades "
                  "each fix changed", "",
                  "| Strategy | Signals | Legacy trades | Corrected trades | "
                  "Fix 1: last-bar entries dropped (was overnight) | "
                  "Fix 2: 8-bar-capped square-offs now run to session end | "
                  "Fix 3: stop fills through gaps (honest fill) | "
                  "Skipped: declared stop unavailable |",
                  "|---|---|---|---|---|---|---|---|"]
        for name, _ in STRATEGIES:
            a = audits[name]
            lt = legacy_res.get(name, {}).get("trades", 0)
            ot = owned_res.get(name, {}).get("trades", 0)
            lines.append(
                f"| {name} | {a['signals']:,} | {lt:,} | {ot:,} | "
                f"{a['fix1_lastbar_entries_dropped']:,} | "
                f"{a['fix2_capped_squareoffs_extended']:,} | "
                f"{a['fix3_gap_fill_stops']:,} | "
                f"{a['owned_skipped_no_stop']:,} |")
        lines += ["", "### Fix definitions", "",
                  "1. **No overnight carry**: signals on the session's last "
                  "bar previously entered at that close and rode the "
                  "overnight gap; they are now untradeable (no same-session "
                  "bar remains to manage them).",
                  "2. **Strategy-owned session exit**: the platform's generic "
                  "8-bar holding cap is gone; intraday positions run to the "
                  "strategy's own exit (stop / target / trail) or the "
                  "session's ACTUAL last bar.",
                  "3. **Honest gap fills**: a bar opening beyond the stop "
                  "fills at the open, not at the stop level (the legacy "
                  "simulator's documented optimism).",
                  "",
                  "**Design change (not a bug fix, quantified separately):** "
                  "exits now follow each strategy's own declared methodology. "
                  "The five strategies with published exits recorded in "
                  "`research/INTRADAY_PRODUCTION_BATCH1.md` / the Phase-17 "
                  "fidelity audit use their structural stops and targets "
                  "(ORB: OR-low stop, 1x-range target; CPR: CPR-bottom stop, "
                  "R1 partial then R2; first-pullback: pullback-low stop, 2R; "
                  "VWAP pair: structural stops, 2R). `pullback_15m` and "
                  "`volexp_1h` have no recorded published exits and declare "
                  "their pre-reset ATR-stop + chandelier-trail behaviour as "
                  "their own. Entry conditions, entry timing (signal-bar "
                  "close), parameters, costs and universe are unchanged.",
                  "", "## Before vs after (same signals, same costs)", "",
                  "| Strategy | Net Rs (legacy) | Net Rs (corrected) | "
                  "PF (legacy) | PF (corrected) | Avg/trade bps (legacy) | "
                  "Avg/trade bps (corrected) | Win % (legacy) | "
                  "Win % (corrected) |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for name, _ in STRATEGIES:
            lr = legacy_res.get(name, {})
            orr = owned_res.get(name, {})
            lines.append(
                f"| {name} | {lr.get('net_profit_rs', 0):+,.0f} | "
                f"{orr.get('net_profit_rs', 0):+,.0f} | "
                f"{lr.get('profit_factor', 0):.3f} | "
                f"{orr.get('profit_factor', 0):.3f} | "
                f"{lr.get('avg_return_per_trade_bps', 0):+.2f} | "
                f"{orr.get('avg_return_per_trade_bps', 0):+.2f} | "
                f"{lr.get('win_rate_pct', 0):.1f} | "
                f"{orr.get('win_rate_pct', 0):.1f} |")

    lines += ["", "## Ranked by net profit (Rs, corrected run)", "",
              "| Rank | Strategy | Net profit (Rs) | Profit factor | "
              "Win rate (%) | Trades | Avg/trade (Rs) | Max DD (Rs) |",
              "|---|---|---|---|---|---|---|---|"]
    for i, name in enumerate(ranked, 1):
        r = results[name]
        lines.append(
            f"| {i} | {name} | {r.get('net_profit_rs', 0):+,.0f} | "
            f"{r.get('profit_factor', 0):.3f} | {r.get('win_rate_pct', 0):.1f} "
            f"| {r.get('trades', 0):,} | "
            f"{r.get('avg_return_per_trade_rs', 0):+,.0f} | "
            f"{r.get('max_drawdown_rs', 0):,.0f} |")
    lines += ["", f"_Period: {results[ranked[0]].get('start')} to "
              f"{results[ranked[0]].get('end')}. Generated by "
              "scripts/backtest_50k.py (--compare). The pre-fix baseline "
              f"remains recorded in `{LEGACY_REPORT.name}`._"]

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nreport: {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
