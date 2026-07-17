"""Console status dashboard - monitoring only, changes nothing.

Renders one plain-text panel per refresh from the paper engine's
``snapshot()`` plus ambient facts (market session state from the IST clock,
universe/eligible counts, scheduler state, health checks). Deliberately not a
TUI framework - it must work in any terminal, any log file, any CI capture.
"""

from __future__ import annotations

from datetime import time as dtime
from typing import Optional

import pandas as pd

IST = "Asia/Kolkata"
SESSION_OPEN = dtime(9, 15)
SESSION_CLOSE = dtime(15, 30)


def market_status(now=None) -> str:
    """OPEN / CLOSED (weekend) / PRE-OPEN / POST-CLOSE by the IST clock.

    Holiday awareness needs the exchange calendar feed - until that data
    source exists this reports the weekday/session-time state only.
    """
    now = (pd.Timestamp(now) if now is not None
           else pd.Timestamp.now(tz="UTC")).tz_convert(IST)
    if now.weekday() >= 5:
        return "CLOSED (weekend)"
    t = now.time()
    if t < SESSION_OPEN:
        return "PRE-OPEN"
    if t <= SESSION_CLOSE:
        return "OPEN"
    return "POST-CLOSE"


def _money(value: float) -> str:
    return f"{value:>12,.0f}"


def render(snapshot: dict, *, universe_size: Optional[int] = None,
           eligible: Optional[int] = None,
           scheduler_state: Optional[dict] = None,
           health: Optional[dict] = None, width: int = 78) -> str:
    """One dashboard frame as a string (caller prints it)."""
    bar = "=" * width
    thin = "-" * width
    lines = [bar]
    now = snapshot.get("as_of", "")
    lines.append(f" ALGO PAPER TRADER          {now}")
    lines.append(f" market: {market_status(snapshot.get('as_of'))}"
                 f"   strategies: {', '.join(snapshot.get('strategies', []))}")
    uni = "n/a" if universe_size is None else universe_size
    eli = "n/a" if eligible is None else eligible
    scan = snapshot.get("last_scan") or {}
    lines.append(f" universe: {uni}   eligible: {eli}   last scan: "
                 f"{scan.get('timeframe', '-')} "
                 f"({scan.get('with_data', 0)}/{scan.get('requested', 0)} "
                 f"symbols, {scan.get('candidates', 0)} candidates, "
                 f"{scan.get('duration_ms', 0):.0f} ms)")
    if scheduler_state:
        due = ", ".join(f"{tf}:{s:.0f}s" for tf, s in scheduler_state.items())
        lines.append(f" next scans due in: {due}")
    lines.append(thin)

    # capital + P&L
    lines.append(
        f" capital  total {_money(snapshot.get('capital_total', 0))}"
        f"   deployed {_money(snapshot.get('capital_deployed', 0))}"
        f"   available {_money(snapshot.get('capital_available', 0))}")
    lines.append(
        f" P&L      realised today {_money(snapshot.get('realized_pnl_today', 0))}"
        f"   unrealised {_money(snapshot.get('unrealized_pnl', 0))}"
        f"   trades today {snapshot.get('trades_today', 0)}")
    lines.append(
        f" risk     daily budget left {_money(snapshot.get('risk_budget_left', 0))}")
    lines.append(thin)

    # open positions
    positions = snapshot.get("positions", [])
    lines.append(f" OPEN POSITIONS ({len(positions)})")
    if positions:
        lines.append(f"  {'symbol':10} {'strategy':14} {'entry':>9} "
                     f"{'last':>9} {'stop':>9} {'stake':>10} {'uPnL':>10} trail")
        for p in positions:
            lines.append(
                f"  {p['symbol']:10} {p['strategy']:14} {p['entry']:>9.2f} "
                f"{p['last']:>9.2f} {p['stop']:>9.2f} {p['stake']:>10,.0f} "
                f"{p['unrealized']:>10,.0f} {'Y' if p['trailed'] else '-'}")
    else:
        lines.append("  (none)")
    lines.append(thin)

    # pending opportunities
    tops = snapshot.get("top_opportunities", [])
    lines.append(f" TOP RANKED OPPORTUNITIES ({len(tops)})")
    for o in tops:
        score = "-" if o.get("score") is None else f"{o['score']:.3f}"
        lines.append(f"  #{o.get('rank', '-'):<3} {o.get('symbol', ''):10} "
                     f"{o.get('strategy', ''):14} score {score} "
                     f"conf {o.get('confidence', 0):.2f}")
    if not tops:
        lines.append("  (none)")
    lines.append(thin)

    # health
    health = health or {}
    checks = "  ".join(f"{k}:{'OK' if v else 'FAIL'}"
                       for k, v in health.items()) or "n/a"
    lines.append(f" health: {checks}")
    lines.append(bar)
    return "\n".join(lines)
