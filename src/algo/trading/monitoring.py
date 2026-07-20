"""Monitoring - health checks, performance metrics, daily summaries, dashboard.

Read-only over the portfolio + event log. Health returns a structured status
the engine logs each cycle; the daily summary is written at square-off; the
dashboard renders a console snapshot (monitoring only, never controls).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from algo.core.logging import get_logger

logger = get_logger("trading.monitoring")


class HealthMonitor:
    def __init__(self, clock, risk) -> None:
        self.clock = clock
        self.risk = risk
        self.last_cycle_ts = None
        self.cycles = 0
        self.errors = 0

    def beat(self, adapter_ok: bool, data_fresh: bool) -> dict:
        self.cycles += 1
        self.last_cycle_ts = pd.Timestamp.now(tz="UTC").isoformat()
        status = "ok"
        if self.risk.tripped:
            status = "halted"
        elif not adapter_ok or not data_fresh:
            status = "degraded"
        return {"status": status, "cycles": self.cycles, "errors": self.errors,
                "adapter_ok": adapter_ok, "data_fresh": data_fresh,
                "risk_tripped": self.risk.tripped,
                "trip_reason": self.risk.trip_reason,
                "last_cycle": self.last_cycle_ts}


def daily_summary(portfolio, mode: str) -> dict:
    stats = portfolio.daily_stats()
    trades = portfolio.closed_trades
    gross_win = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_loss = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    stats.update({
        "mode": mode,
        "profit_factor": round(gross_win / gross_loss, 3)
        if gross_loss > 0 else None,
        "gross_profit": round(gross_win, 2),
        "gross_loss": round(gross_loss, 2),
        "by_strategy": _by_strategy(trades),
    })
    return stats


def _by_strategy(trades) -> dict:
    out: dict = {}
    for t in trades:
        s = out.setdefault(t["strategy"], {"trades": 0, "pnl": 0.0})
        s["trades"] += 1
        s["pnl"] = round(s["pnl"] + t["pnl"], 2)
    return out


def write_summary(state_dir, summary: dict) -> Path:
    d = Path(state_dir)
    d.mkdir(parents=True, exist_ok=True)
    day = summary.get("session") or pd.Timestamp.now(tz="UTC").strftime(
        "%Y-%m-%d")
    path = d / f"summary-{day}.json"
    path.write_text(json.dumps(summary, indent=1, default=str),
                    encoding="utf-8")
    return path


def render_dashboard(portfolio, health: dict, mode: str) -> str:
    lines = [
        "=" * 64,
        f" PRODUCTION TRADING  [{mode.upper()}]  status={health['status']}",
        "=" * 64,
        f" cycles={health['cycles']} errors={health['errors']} "
        f"risk_tripped={health['risk_tripped']}",
    ]
    s = portfolio.daily_stats()
    lines.append(f" realized={s['realized_pnl']:+.0f}  "
                 f"unrealized={s['unrealized_pnl']:+.0f}  "
                 f"deployed={s['deployed_capital']:.0f}")
    lines.append(f" open={s['open_positions']} closed={s['closed_trades']} "
                 f"wins={s['wins']} losses={s['losses']}")
    lines.append("-" * 64)
    for p in portfolio.open_positions():
        lines.append(f" {p.symbol:12} {p.strategy:16} qty={p.open_quantity:.1f} "
                     f"entry={p.entry_price:.2f} stop={p.stop:.2f} "
                     f"uPnL={p.unrealized():+.0f}")
    if not portfolio.open_positions():
        lines.append(" (no open positions)")
    lines.append("=" * 64)
    return "\n".join(lines)
