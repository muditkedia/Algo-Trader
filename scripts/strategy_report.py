"""Strategy activation report - which strategies scan live, and why.

Answers "the library registers N strategies but the engine scans M" with a
per-strategy breakdown taken from the live registry (never a hand-written
list), so it can never drift from the code.

    .venv/Scripts/python scripts/strategy_report.py
    .venv/Scripts/python scripts/strategy_report.py --markdown > docs/x.md
"""

from __future__ import annotations

import argparse
import sys

from algo.core.enums import HoldingScope
from algo.strategies.library import ALL_STRATEGIES
from algo.trading.engine import load_intraday_strategies

INTRADAY_REASON = ("intraday: squares off before the close, so the live "
                   "intraday engine executes it as designed")
SWING_REASON = ("swing/positional: holds overnight for {n} bars (delivery). "
                "The live engine is INTRADAY - it force-closes every position "
                "at square-off - so scanning it here would systematically "
                "misexecute the strategy")


def rows() -> list:
    scanning = {s.name for s in load_intraday_strategies()}
    out = []
    for cls in ALL_STRATEGIES:
        m = cls.meta
        intraday = m.holding_scope == HoldingScope.INTRADAY
        out.append({
            "name": m.name,
            "timeframe": m.timeframe,
            "scope": m.holding_scope.value,
            "enabled": bool(m.enabled),
            "scanning": m.name in scanning,
            "reason": (INTRADAY_REASON if intraday
                       else SWING_REASON.format(n=m.max_hold_bars)),
        })
    return sorted(out, key=lambda r: (not r["scanning"], r["name"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args()

    data = rows()
    scanning = [r for r in data if r["scanning"]]
    if args.markdown:
        print(f"**{len(data)} registered · {len(scanning)} scanning live**\n")
        print("| Strategy | Timeframe | Scope | Enabled | Scans live | Reason |")
        print("|---|---|---|---|---|---|")
        for r in data:
            print(f"| `{r['name']}` | {r['timeframe']} | {r['scope']} | "
                  f"{'yes' if r['enabled'] else 'NO'} | "
                  f"{'**yes**' if r['scanning'] else 'no'} | {r['reason']} |")
        return 0

    print(f"{len(data)} registered strategies, {len(scanning)} scan live\n")
    print(f"{'STRATEGY':22} {'TF':5} {'SCOPE':9} {'ENABLED':8} SCANS")
    print("-" * 60)
    for r in data:
        print(f"{r['name']:22} {r['timeframe']:5} {r['scope']:9} "
              f"{'yes' if r['enabled'] else 'NO':8} "
              f"{'YES' if r['scanning'] else 'no'}")
    print("\nExcluded strategies are daily swing systems; see the reason "
          "column with --markdown.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
