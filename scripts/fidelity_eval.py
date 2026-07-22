"""Phase 17 Parts D+E: production vs fidelity execution for the five baselines.

Runs BOTH engines from the SAME prepared signals so trades match 1:1, then:
  Part D - full-universe comparison (gross/net expectancy, win rate, PF, maxDD,
           trade count, matched-pair entry/exit improvement in bps)
  Part E - component-attribution ladder on a 40-symbol subset:
           L0 production (close entry, ATR stop+trail, no target)
           L1 published ENTRY only  (trigger entry, ATR stop+trail, no target)
           L2 + published STOP      (trigger entry, structural stop, ATR trail)
           L3 full published spec   (+ targets/partials, trail off)
           deltas attribute entry / stop / target+trail-swap fidelity.

No optimisation, no tuning: the specs are the D-036 published forms, fixed.
Retired ``orb_15m`` and ``first_pullback_15m`` are excluded because running
their canonical replacements against frozen 15-minute fidelity specs would
falsely attribute legacy evidence to the new implementations.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from algo.core.costs import NseEquityCostModel, Product
from algo.core.logging import configure
from algo.data.store import MarketDataStore
from algo.evidence.database import EvidenceDB, MEMORY
from algo.research.engine import ResearchEngine
from algo.research.fidelity import FIDELITY_SPECS, run_fidelity
from algo.research.validation import metrics
from algo.strategies.library import (
    CprBreakout,
    VwapPullback, VwapTrendContinuation,
)

ROOT = Path("user_data")
REPORT = ROOT / "backtest_results" / "reports" / "fidelity_eval.md"
CAPITAL = 100_000.0
COST = NseEquityCostModel()

STRATEGIES = [("vwap_pullback_15m", VwapPullback),
              ("vwap_15m", VwapTrendContinuation),
              ("cpr_breakout_15m", CprBreakout)]


def _stats(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"n": 0}
    m = metrics.summarize(trades, CAPITAL)
    return {"n": len(trades),
            "gross_bps": round(float(trades["gross_ratio"].mean()) * 1e4, 1),
            "net_bps": round(float(trades["profit_ratio"].mean()) * 1e4, 1),
            "win": round(m["win_rate"], 3), "pf": round(m["profit_factor"], 3),
            "maxdd": round(m["max_drawdown_pct"], 3)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols-file", default="nifty100.txt")
    parser.add_argument("--subset", type=int, default=40,
                        help="symbols for the Part-E ladder")
    args = parser.parse_args()
    configure(level=logging.ERROR)

    store = MarketDataStore(str(ROOT / "data" / "nse"))
    have = set(store.symbols("15m"))
    symbols = [s.strip().upper() for s in Path(args.symbols_file).read_text()
               .splitlines() if s.strip() and not s.startswith("#")]
    symbols = [s for s in symbols if s in have]
    subset = sorted(symbols)[:args.subset]

    db = EvidenceDB(MEMORY)
    eng = ResearchEngine(db, store=store, cost_model=COST)

    lines = ["# Fidelity evaluation - production vs published execution", "",
             f"_{len(symbols)} symbols full compare; {len(subset)}-symbol "
             "ladder for attribution. Specs = D-036 published forms, fixed._",
             ""]
    print(f"fidelity eval: {len(symbols)} symbols (D), {len(subset)} (E)\n")

    ladder_rows = {}
    for name, cls in STRATEGIES:
        strat = cls()
        spec = FIDELITY_SPECS[name]
        prepared = eng.prepare_signals(strat, symbols)

        prod = eng.simulate_strategy(strat, symbols, product=Product.INTRADAY,
                                     persist=False, prepared=prepared)
        fid = run_fidelity(prepared.frames, prepared.signals, spec,
                           cost_model=COST)
        sp, sf = _stats(prod), _stats(fid)

        # matched-pair improvements: join on (pair, open_date)
        imp = {}
        if not prod.empty and not fid.empty:
            # production entry price = stake / quantity is not in the frame;
            # entry price equals signal-bar close == stake_amount/quantity; we
            # recover entry/exit from ratios: exit = entry*(1+gross). Merge the
            # fidelity fills against the prepared frames' close at open_date.
            fmap = fid.set_index(["pair", "open_date"])
            rows = []
            for sym, frame in prepared.frames.items():
                closes = frame.set_index("date")["close"]
                sig = prepared.signals[sym]
                for ts in frame["date"][sig.to_numpy(bool)]:
                    key = (sym, ts)
                    if key in fmap.index:
                        prod_entry = float(closes.loc[ts])
                        f = fmap.loc[key]
                        rows.append(((prod_entry - float(f["entry_price"]))
                                     / prod_entry * 1e4))
            if rows:
                imp = {"entry_improvement_bps": round(float(np.mean(rows)), 1),
                       "matched": len(rows)}

        exit_imp = None
        if sp.get("n") and sf.get("n"):
            # exit improvement = total improvement - entry improvement
            total = sf["gross_bps"] - sp["gross_bps"]
            exit_imp = round(total - imp.get("entry_improvement_bps", 0.0), 1)

        print(f"=== {name} ===")
        print(f"  prod: {sp}")
        print(f"  fid : {sf}")
        print(f"  entry_improvement {imp.get('entry_improvement_bps')} bps "
              f"(matched {imp.get('matched')}) | exit_improvement {exit_imp} bps")
        lines += [f"## {name}", "",
                  f"- production: `{sp}`", f"- fidelity:  `{sf}`",
                  f"- avg entry improvement: **{imp.get('entry_improvement_bps')}"
                  f" bps** (matched {imp.get('matched')}); "
                  f"avg exit improvement: **{exit_imp} bps**", ""]

        # ---- Part E ladder on the subset --------------------------------
        prep_s = eng.prepare_signals(strat, subset)
        l0 = eng.simulate_strategy(strat, subset, product=Product.INTRADAY,
                                   persist=False, prepared=prep_s)
        l1_spec = dataclasses.replace(spec, stop_mode="atr", stop_col=None,
                                      target_mode="none", target_col=None,
                                      partial_fraction=0.0, trail="atr")
        l2_spec = dataclasses.replace(spec, target_mode="none", target_col=None,
                                      partial_fraction=0.0, trail="atr")
        l1 = run_fidelity(prep_s.frames, prep_s.signals, l1_spec,
                          cost_model=COST)
        l2 = run_fidelity(prep_s.frames, prep_s.signals, l2_spec,
                          cost_model=COST)
        l3 = run_fidelity(prep_s.frames, prep_s.signals, spec, cost_model=COST)
        g = [(_stats(x).get("net_bps") if len(x) else None)
             for x in (l0, l1, l2, l3)]
        ladder_rows[name] = {
            "L0_prod": g[0], "L1_entry": g[1], "L2_stop": g[2], "L3_full": g[3],
            "d_entry": None if None in g[:2] else round(g[1] - g[0], 1),
            "d_stop": None if None in g[1:3] else round(g[2] - g[1], 1),
            "d_target": None if None in g[2:4] else round(g[3] - g[2], 1)}
        print(f"  ladder(net bps): {ladder_rows[name]}\n")

    ldf = pd.DataFrame(ladder_rows).T
    lines += ["## Part E - attribution ladder (net bps/trade, "
              f"{len(subset)}-symbol subset)", "", "```", ldf.to_string(),
              "```", "",
              "_d_entry = published trigger entry vs close entry (ATR exits "
              "held constant); d_stop = structural stop vs ATR stop; d_target "
              "= targets/partials replacing the ATR trail._"]

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nladder:\n{ldf.to_string()}")
    print(f"\nreport: {REPORT}")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
