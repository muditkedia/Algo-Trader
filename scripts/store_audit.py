"""Store audit CLI - integrity, gap and completeness validation.

Thin wrapper over ``algo.data.audit`` (DATA_INFRASTRUCTURE_PLAN.md section 6),
run after every bulk data operation. Writes a markdown report and exits
non-zero on hard failures, so it can gate later automation.

    .venv/Scripts/python scripts/store_audit.py
    .venv/Scripts/python scripts/store_audit.py --timeframes 15m,5m
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from algo.data.audit import audit_timeframe, context_completeness, \
    cross_timeframe
from algo.data.manifest import manifest_path
from algo.data.store import MarketDataStore

REPORT = Path("user_data/backtest_results/reports/store_audit.md")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default="user_data/data/nse")
    parser.add_argument("--timeframes", default=None,
                        help="comma list; default: all directories found")
    parser.add_argument("--report", default=str(REPORT))
    args = parser.parse_args()
    store = MarketDataStore(args.store)
    tfs = ([t.strip() for t in args.timeframes.split(",") if t.strip()]
           if args.timeframes else store.timeframes())

    lines = ["# Store audit", "",
             f"_Store: `{args.store}` · audited "
             f"{pd.Timestamp.now(tz='UTC').isoformat(timespec='seconds')}_",
             ""]
    hard_fail = False
    for tf in tfs:
        r = audit_timeframe(store, tf)
        cf, cw = context_completeness(store, tf)
        r["failures"] += cf
        r["warnings"] += cw
        lines += [f"## {tf} - {r['symbols']} symbols, "
                  f"{r['union_sessions']} union sessions "
                  f"({r.get('calendar_start', '-')} -> "
                  f"{r.get('calendar_end', '-')})", ""]
        if r["special_sessions"]:
            lines.append(f"- special/short sessions (market-wide, "
                         f"self-identified): {len(r['special_sessions'])} -> "
                         f"{', '.join(r['special_sessions'])}")
        lines += [f"- **FAIL** {f}" for f in r["failures"]]
        lines += [f"- warn: {w}" for w in r["warnings"]]
        if not r["failures"] and not r["warnings"] \
                and not r["special_sessions"]:
            lines.append("- clean")
        hard_fail = hard_fail or bool(r["failures"])
        lines.append("")
        print(f"{tf}: {r['symbols']} symbols, {r['union_sessions']} sessions, "
              f"{len(r['failures'])} failures, {len(r['warnings'])} warnings, "
              f"{len(r['special_sessions'])} special sessions")

    if "5m" in tfs and "15m" in tfs:
        notes = cross_timeframe(store, "5m", "15m")
        lines += ["## Cross-timeframe (5m vs 15m sessions)", ""]
        lines += [f"- warn: {n}" for n in notes] or ["- clean"]
        lines.append("")
        for n in notes:
            print("warn:", n)

    has_manifest = manifest_path(store).exists()
    lines.append(f"Manifest present: {'yes' if has_manifest else 'NO'}")
    if not has_manifest:
        print("warn: no manifest.json at the store root")

    verdict = "FAIL" if hard_fail else "PASS"
    lines += ["", f"**Audit verdict: {verdict}**"]
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\naudit verdict: {verdict}  (report: {report_path})")
    return 1 if hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())
