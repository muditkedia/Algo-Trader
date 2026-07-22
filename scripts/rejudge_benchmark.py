"""Re-judge every strategy under the benchmark amendment (D-031, Part C).

Re-evaluates each discovered strategy from the SAME stored bars - no change to
parameters, horizons, indicators, exits or costs - applying the amended gate
(selection edge over a random entry must clear cost) plus the benchmark
battery. Persists a NEW evaluation generation (a fresh run, scope
``benchmark``) so historical verdicts are kept, not overwritten, and records a
status transition only where the amended verdict maps to a different lifecycle
status.

    .venv/Scripts/python scripts/rejudge_benchmark.py --store-dir user_data/data/nse
        [--symbols-file nifty100.txt] [--db PATH] [--dry-run]

``--dry-run`` computes and reports old-vs-new WITHOUT writing (used to preview
against a copy of the evidence DB before touching the production one).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


from algo.core.costs import NseEquityCostModel
from algo.core.logging import configure, get_logger
from algo.data.store import MarketDataStore
from algo.evidence.database import EvidenceDB
from algo.evidence.logger import EvidenceLogger
from algo.research import reporting
from algo.research.engine import ResearchEngine, product_for_strategy
from algo.strategies.library import ALL_STRATEGIES

logger = get_logger("scripts.rejudge")


def _stored_trade_metrics(db: EvidenceDB, name: str) -> dict:
    """Latest stored absolute trade metrics for a strategy (no re-simulation).

    The amendment is additive, so the absolute §7 numbers a prior real
    measurement recorded are still valid - reuse them rather than re-simulate
    tens of thousands of intraday trades to re-derive an unchanged result.
    """
    row = db.connection.execute(
        "SELECT e.n_trades, e.net_expectancy, e.profit_factor "
        "FROM evaluations e JOIN strategies s USING (strategy_id) "
        "WHERE s.name = ? AND e.scope_type = 'overall' "
        "ORDER BY e.as_of DESC LIMIT 1", (name,)).fetchone()
    if row is None:
        return {}
    return {"trades": row["n_trades"], "expectancy": row["net_expectancy"],
            "profit_factor": row["profit_factor"]}

ROOT = Path("user_data")
DEFAULT_DB = ROOT / "evidence" / "evidence.db"
REPORT = ROOT / "backtest_results" / "reports" / "benchmark_rejudge.md"

#: Amended verdict -> lifecycle status. A strategy earns a promotable status
#: only on a genuine (selection-backed) PASS.
STATUS_MAP = {"PASS": "measured", "FAIL": "rejected",
              "BORDERLINE": "draft", "INCONCLUSIVE": "draft"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-dir", default=str(ROOT / "data" / "nse"))
    parser.add_argument("--symbols-file", default="nifty100.txt")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--dry-run", action="store_true",
                        help="report old-vs-new without writing")
    args = parser.parse_args()
    configure(level=logging.WARNING)

    store = MarketDataStore(args.store_dir)
    if Path(args.symbols_file).exists():
        symbols = [s.strip().upper() for s in
                   Path(args.symbols_file).read_text().splitlines()
                   if s.strip() and not s.startswith("#")]
    else:
        symbols = sorted(set().union(*(set(store.symbols(tf))
                                       for tf in ("15m", "1h", "1d"))))

    db = EvidenceDB(args.db)
    log = EvidenceLogger(db)
    engine = ResearchEngine(db, store=store, cost_model=NseEquityCostModel())

    # OLD state: the current lifecycle status per strategy name.
    old_status = {r["name"]: r["status"] for r in db.connection.execute(
        "SELECT name, status FROM strategies").fetchall()}

    strategies = [cls() for cls in ALL_STRATEGIES]
    # Daily strategies get the FULL amended battery (re-simulation + managed
    # benchmarks); the intraday incumbents - already rejected on absolute
    # grounds (D-026), tens of thousands of trades each - get the fast
    # selection-gate confirmation on fresh edge + their STORED absolute metrics.
    # The amendment only ADDS a necessary condition, so a strategy that already
    # failed the absolute bars on this same data cannot now PASS; re-simulating
    # it would change nothing and cost 30+ minutes.
    daily = [s for s in strategies if s.meta.timeframe == "1d"]
    intraday = [s for s in strategies if s.meta.timeframe != "1d"]
    print(f"re-judging {len(strategies)} strategies on {len(symbols)} symbols "
          f"({'DRY RUN' if args.dry_run else 'writing'}): "
          f"{len(daily)} daily (full battery), {len(intraday)} intraday "
          f"(selection-gate confirmation)\n")

    run_id = None
    if not args.dry_run:
        run = log.start_run("benchmark_rejudge",
                            data_window=f"{args.store_dir}",
                            notes="D-031 amended gate re-judgement")
        run_id = run.run_id

    verdicts = engine.measure_all(daily, symbols, benchmarks=True)
    for s in intraday:
        edge = engine.measure_edge(
            s, symbols, product=product_for_strategy(s),
            prepared=engine.prepare_signals(s, symbols))
        stored = _stored_trade_metrics(db, s.name)
        verdicts.append(engine.verdict_for(s, edge, stored, {}, {}))

    changes = []
    for v in verdicts:
        row = v.as_row()
        version = next(s.meta.version for s in strategies if s.name == v.strategy)
        sid = log.register_strategy(v.strategy, version)
        old = old_status.get(v.strategy, "(none)")
        new_status = STATUS_MAP[v.verdict]
        changed = new_status != old
        changes.append({"strategy": v.strategy, "old_status": old,
                        "new_verdict": v.verdict, "new_status": new_status,
                        "changed": changed,
                        "selection_bps": row.get("selection_bps"),
                        "selection_ci_low_bps": row.get("selection_ci_low_bps"),
                        "reason": v.reasons[0] if v.reasons else ""})
        if not args.dry_run:
            # new evaluation generation - append, never overwrite
            m = v.trade_metrics or {}
            log.record_evaluation(
                sid, "benchmark", "all", run_id=run_id,
                n_signals=row.get("n_signals"), n_trades=m.get("trades"),
                net_expectancy=m.get("expectancy"),
                win_rate=m.get("win_rate"),
                profit_factor=m.get("profit_factor"),
                ci_lo=row.get("selection_ci_low_bps"),
                verdict=v.verdict,
                mc_gates=json.dumps({"benchmarks": v.benchmarks,
                                     "selection_bps": row.get("selection_bps")}))
            log.set_strategy_status(
                sid, new_status,
                reason=f"D-031 re-judge {v.verdict}: "
                       + "; ".join(v.reasons)[:400])

    table = engine.league_table(verdicts)
    print(reporting.league_table_text(table))
    print("\nOLD -> NEW\n")
    for c in changes:
        flag = "  ** CHANGED **" if c["changed"] else ""
        print(f"  {c['strategy']:22} {c['old_status']:9} -> "
              f"{c['new_verdict']:11} ({c['new_status']}){flag}")
        print(f"      selection {c['selection_bps']} bps "
              f"(CI-low {c['selection_ci_low_bps']}); {c['reason']}")

    if not args.dry_run:
        log.finish_run(run_id)
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        lines = [reporting.league_table_markdown(
            table, verdicts, data_note="D-031 amended-gate re-judgement "
            f"(real data, {len(symbols)} symbols)",
            title="Benchmark re-judgement league table"),
            "", "## Old status -> new verdict", ""]
        for c in changes:
            flag = " **CHANGED**" if c["changed"] else ""
            lines.append(f"- **{c['strategy']}**: {c['old_status']} -> "
                         f"{c['new_verdict']} ({c['new_status']}){flag} - "
                         f"selection {c['selection_bps']} bps "
                         f"(CI-low {c['selection_ci_low_bps']})")
        REPORT.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nreport: {REPORT}")

    db.close()
    n_changed = sum(c["changed"] for c in changes)
    print(f"\n{n_changed} status change(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
