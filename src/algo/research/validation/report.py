"""Standardized Markdown report generator (VALIDATION_RULES SS19).

Renders the fixed 18-section report from a ``ReportInputs`` bundle. Any
component not yet available (e.g. sensitivity before Phase E) renders as an
explicit ``PENDING`` block rather than being silently omitted, so a report
is always structurally complete and auditable.

The verdict section applies the SS7 gates to whatever inputs exist and
returns PASS / FAIL / INCONCLUSIVE with a per-gate table.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from .logging_utils import get_logger

logger = get_logger("report")

SECTION_TITLES = (
    "Executive Summary", "Configuration Used", "Data Window",
    "Validation Gates", "Performance Metrics", "Risk Metrics",
    "Portfolio Analysis", "Holding-Time Distribution", "Monthly Returns",
    "Drawdown Analysis", "Exit Reason Analysis",
    "GO/NO-GO Rejection Statistics", "Regime Breakdown",
    "Sensitivity Analysis", "Stress-Test Results", "Monte Carlo Results",
    "Final Verdict", "Recommended Next Action",
)

#: SS7 point-estimate gates applied by the verdict (CI gates come from MC).
POINT_GATES = {
    "profit_factor": ("min", 1.25),
    "sharpe": ("min", 1.0),
    "sortino": ("min", 1.3),
    "recovery_factor": ("min", 2.0),
    "expectancy": ("min_exclusive", 0.0),
    "max_drawdown_pct": ("max", 0.25),
    "calmar": ("min", 1.0),
}
MIN_OOS_TRADES = 150


@dataclass
class ReportInputs:
    """Everything the report can render; every field optional but named."""

    title: str = "Backtest Validation Report"
    config_snapshot: Optional[dict] = None
    command: str = ""
    data_window: Optional[dict] = None          # pairs, timeframes, timerange...
    gates: Optional[dict] = None                # lookahead/recursive/smoke status
    metrics: Optional[dict] = None              # metrics.summarize output
    portfolio: Optional[dict] = None            # portfolio.analyze output
    holding: Optional[dict] = None              # metrics.holding_time_stats
    monthly: Optional[pd.Series] = None         # metrics.monthly_returns
    drawdowns: Optional[list] = None            # metrics.top_drawdowns
    exit_reasons: Optional[dict] = None         # counts / pnl by exit tag
    rejections: Optional[pd.DataFrame] = None   # loaders.load_rejections
    regime: Optional[dict] = None               # regime.regime_breakdown
    sensitivity: Optional[dict] = None          # sensitivity.aggregate
    stress: Optional[dict] = None               # stress.run_all
    mc: Optional[dict] = None                   # MonteCarloResult.as_dict()
    next_action: str = ""
    context_note: str = ""                      # e.g. "SELF-TEST - synthetic data"


def _pending(what: str) -> str:
    return f"_PENDING - {what}._\n"


def _dict_table(data: dict, keys: Optional[list] = None) -> str:
    keys = keys or list(data.keys())
    lines = ["| Metric | Value |", "|---|---|"]
    for key in keys:
        if key in data and not isinstance(data[key], (dict, list, pd.DataFrame)):
            lines.append(f"| {key} | {data[key]} |")
    return "\n".join(lines) + "\n"


def _json_block(data, limit: int = 4000) -> str:
    text = json.dumps(data, indent=1, default=str)
    if len(text) > limit:
        text = text[:limit] + "\n... (truncated)"
    return f"```json\n{text}\n```\n"


def compute_verdict(inputs: ReportInputs) -> dict:
    """Apply SS7 gates to the available inputs -> PASS/FAIL/INCONCLUSIVE."""
    rows, failures, inconclusive = [], [], []
    metrics = inputs.metrics or {}

    if metrics.get("trades", 0) < MIN_OOS_TRADES:
        inconclusive.append(
            f"trades {metrics.get('trades', 0)} < {MIN_OOS_TRADES} minimum"
        )
    for name, (kind, bound) in POINT_GATES.items():
        value = metrics.get(name)
        if value is None:
            inconclusive.append(f"{name} unavailable")
            continue
        ok = (value >= bound if kind == "min"
              else value > bound if kind == "min_exclusive"
              else value <= bound)
        rows.append({"gate": name, "bound": f"{kind} {bound}",
                     "value": value, "pass": bool(ok)})
        if not ok:
            failures.append(name)

    if inputs.mc and inputs.mc.get("gates"):
        for gate, payload in inputs.mc["gates"].items():
            if not isinstance(payload, dict):
                continue
            rows.append({"gate": f"mc.{gate}", "bound": "CI",
                         "value": payload.get("value"),
                         "pass": payload.get("pass")})
            if payload.get("pass") is False:
                failures.append(gate)
    if inputs.holding:
        ok = bool(inputs.holding.get("iqr_within_design_band"))
        rows.append({"gate": "holding_iqr_20_150", "bound": "band",
                     "value": f"{inputs.holding.get('p25')}-"
                              f"{inputs.holding.get('p75')} min",
                     "pass": ok})
        if not ok:
            failures.append("holding_time")
    if inputs.portfolio:
        ok = bool(inputs.portfolio.get("worst_case_stop_within_15pct"))
        rows.append({
            "gate": "portfolio_worst_case_stop_le_15pct", "bound": "<=0.15",
            "value": inputs.portfolio.get(
                "worst_case_simultaneous_stop_pct_assumed"),
            "pass": ok})
        if inputs.portfolio.get("worst_case_stop_reject_over_20pct"):
            failures.append("portfolio_worst_case_stop")
    if inputs.regime and inputs.regime.get("stability_gate"):
        gate = inputs.regime["stability_gate"]
        rows.append({"gate": "regime_stability", "bound": "SS7",
                     "value": json.dumps(gate.get("regimes_below_pf_0.8", {})),
                     "pass": gate.get("pass")})
        if gate.get("pass") is False:
            failures.append("regime_stability")

    status = ("FAIL" if failures
              else "INCONCLUSIVE" if inconclusive else "PASS")
    return {"status": status, "rows": rows, "failures": failures,
            "inconclusive": inconclusive}


def generate(inputs: ReportInputs, out_path: Path) -> Path:
    """Render the 18-section report to ``out_path``; returns the path."""
    verdict = compute_verdict(inputs)
    sections = {
        "Executive Summary": _executive_summary(inputs, verdict),
        "Configuration Used": _configuration(inputs),
        "Data Window": (_json_block(inputs.data_window)
                        if inputs.data_window else _pending("data window metadata")),
        "Validation Gates": (_json_block(inputs.gates) if inputs.gates
                             else _pending("Gate 0 (lookahead/recursive) not yet run")),
        "Performance Metrics": (_dict_table(inputs.metrics)
                                if inputs.metrics else _pending("backtest metrics")),
        "Risk Metrics": _risk(inputs),
        "Portfolio Analysis": (_json_block(inputs.portfolio)
                               if inputs.portfolio else _pending("portfolio battery")),
        "Holding-Time Distribution": (_holding(inputs.holding)
                                      if inputs.holding else _pending("holding stats")),
        "Monthly Returns": _monthly(inputs.monthly),
        "Drawdown Analysis": (_json_block(inputs.drawdowns)
                              if inputs.drawdowns else _pending("drawdown episodes")),
        "Exit Reason Analysis": (_json_block(inputs.exit_reasons)
                                 if inputs.exit_reasons else _pending("exit reasons")),
        "GO/NO-GO Rejection Statistics": _rejections(inputs.rejections),
        "Regime Breakdown": (_json_block(inputs.regime)
                             if inputs.regime else _pending(
                                 "regime labels (requires daily OHLCV)")),
        "Sensitivity Analysis": (_sensitivity(inputs.sensitivity)
                                 if inputs.sensitivity else _pending(
                                     "Phase E sensitivity sweep")),
        "Stress-Test Results": (_json_block(inputs.stress)
                                if inputs.stress else _pending("stress scenarios")),
        "Monte Carlo Results": (_json_block(inputs.mc)
                                if inputs.mc else _pending("Monte-Carlo resampling")),
        "Final Verdict": _verdict(verdict),
        "Recommended Next Action": (inputs.next_action or _pending(
            "set by the coordinator / analyst")),
    }

    lines = [f"# {inputs.title}", "",
             f"_Generated {datetime.now(timezone.utc).isoformat()} - "
             f"VALIDATION_RULES.md SS19._", ""]
    if inputs.context_note:
        lines += [f"> **NOTE:** {inputs.context_note}", ""]
    for i, title in enumerate(SECTION_TITLES, start=1):
        lines += [f"## {i}. {title}", "", sections[title], ""]

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("report written: %s (verdict %s)", out_path, verdict["status"])
    return out_path


# ------------------------------------------------------------ section bodies

def _executive_summary(inputs: ReportInputs, verdict: dict) -> str:
    metrics = inputs.metrics or {}
    return (
        f"**Verdict: {verdict['status']}.** "
        f"Trades: {metrics.get('trades', 'n/a')}, "
        f"PF: {metrics.get('profit_factor', 'n/a')}, "
        f"Sharpe: {metrics.get('sharpe', 'n/a')}, "
        f"Sortino: {metrics.get('sortino', 'n/a')}, "
        f"MaxDD: {metrics.get('max_drawdown_pct', 'n/a')}, "
        f"Expectancy: {metrics.get('expectancy', 'n/a')}, "
        f"CAGR (context only): {metrics.get('cagr', 'n/a')}.\n"
    )


def _configuration(inputs: ReportInputs) -> str:
    parts = []
    if inputs.command:
        parts.append(f"Command: `{inputs.command}`\n")
    if inputs.config_snapshot is not None:
        parts.append(_json_block(inputs.config_snapshot))
    return "\n".join(parts) or _pending("config snapshot")


def _risk(inputs: ReportInputs) -> str:
    if not inputs.metrics:
        return _pending("risk metrics")
    keys = ["max_drawdown_pct", "max_drawdown_abs", "max_drawdown_days",
            "recovery_factor", "calmar", "max_consecutive_losses",
            "quarterly_positive_share"]
    return _dict_table(inputs.metrics, keys)


def _holding(holding: dict) -> str:
    keys = ["count", "mean", "median", "p25", "p75", "p90", "min", "max",
            "median_within_design_band", "iqr_within_design_band",
            "bimodal_flag", "share_below_15m", "share_above_180m"]
    body = _dict_table(holding, keys)
    if holding.get("breakdown"):
        body += "\nPer exit reason:\n" + _json_block(holding["breakdown"])
    return body


def _monthly(monthly: Optional[pd.Series]) -> str:
    if monthly is None or monthly.empty:
        return _pending("monthly returns")
    lines = ["| Month | Return |", "|---|---|"]
    for ts, value in monthly.items():
        lines.append(f"| {ts:%Y-%m} | {value:+.4f} |")
    return "\n".join(lines) + "\n"


def _rejections(rejections: Optional[pd.DataFrame]) -> str:
    if rejections is None or rejections.empty:
        return _pending(
            "no trade_rejections.jsonl records for this window "
            "(populated in dry-run/live and via confirm_trade_entry)"
        )
    reasons: dict = {}
    for entry in rejections.get("rejection_reasons", []):
        for reason in entry or []:
            key = reason.split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
    summary = {
        "total_rejections": int(len(rejections)),
        "by_check": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
    }
    return _json_block(summary)


def _sensitivity(sens: dict) -> str:
    body = f"**Verdict:** {sens.get('verdict', 'n/a')}\n\nTornado (top swings):\n"
    lines = ["| Param | Swing |", "|---|---|"]
    for row in sens.get("tornado", [])[:15]:
        lines.append(f"| {row['param']} | {row['swing']} |")
    body += "\n".join(lines) + "\n"
    if sens.get("cliffs"):
        body += "\nCliffs:\n" + _json_block(sens["cliffs"])
    return body


def _verdict(verdict: dict) -> str:
    lines = [f"**{verdict['status']}**", "",
             "| Gate | Bound | Value | Pass |", "|---|---|---|---|"]
    for row in verdict["rows"]:
        lines.append(f"| {row['gate']} | {row['bound']} | {row['value']} "
                     f"| {row['pass']} |")
    if verdict["failures"]:
        lines += ["", f"Failed gates: {', '.join(verdict['failures'])}"]
    if verdict["inconclusive"]:
        lines += ["", f"Inconclusive: {'; '.join(verdict['inconclusive'])}"]
    return "\n".join(lines) + "\n"
