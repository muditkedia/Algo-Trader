"""Monte-Carlo analysis with confidence intervals (VALIDATION_RULES SS14).

Resamples the realized trade sequence N times and reports 90%/95% CIs for
CAGR, Sharpe, Max Drawdown, Expectancy and Profit Factor, plus the losing
streak distribution used by the stress tests (SS15).

Two resamplers per the protocol:
* iid bootstrap        - trade P&L resampled with replacement (distributional
                         metrics: expectancy, Sharpe, PF).
* stationary bootstrap - circular blocks with geometric lengths (Politis &
                         Romano), preserving short-run autocorrelation
                         (path-dependent metrics: Max Drawdown, CAGR).

Methodology notes:
* Resamples use per-trade ``profit_abs`` on a fixed starting capital
  (matching the fixed-stake config), equity = capital + cumsum.
* Per-resample Sharpe is trade-based (mean/std * sqrt(trades_per_year)) -
  a documented approximation; the point-estimate Sharpe in metrics.py uses
  daily aggregation. CIs are for the resampling distribution, not a mix.
* Deterministic under ``seed``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from validation.logging_utils import get_logger

logger = get_logger("monte_carlo")

CI_LEVELS = {"90": (0.05, 0.95), "95": (0.025, 0.975)}


@dataclass
class MonteCarloResult:
    n_resamples: int
    n_trades: int
    ci: dict = field(default_factory=dict)       # metric -> {p2.5,p5,median,p95,p97.5}
    gates: dict = field(default_factory=dict)    # SS7 CI-bound gates
    losing_streak: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "n_resamples": self.n_resamples, "n_trades": self.n_trades,
            "ci": self.ci, "gates": self.gates,
            "losing_streak": self.losing_streak,
        }


def _stationary_bootstrap_indices(
    n_trades: int, n_resamples: int, mean_block: float, rng: np.random.Generator
) -> np.ndarray:
    """Index matrix (n_resamples x n_trades) via the stationary bootstrap."""
    prob_new_block = 1.0 / mean_block
    starts = rng.integers(0, n_trades, size=(n_resamples, n_trades))
    new_block = rng.random(size=(n_resamples, n_trades)) < prob_new_block
    new_block[:, 0] = True
    indices = np.zeros((n_resamples, n_trades), dtype=np.int64)
    for col in range(n_trades):
        if col == 0:
            indices[:, 0] = starts[:, 0]
            continue
        continued = (indices[:, col - 1] + 1) % n_trades
        indices[:, col] = np.where(new_block[:, col], starts[:, col], continued)
    return indices


def _percentiles(values: np.ndarray) -> dict:
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {}
    return {
        "p2.5": round(float(np.percentile(values, 2.5)), 4),
        "p5": round(float(np.percentile(values, 5)), 4),
        "median": round(float(np.percentile(values, 50)), 4),
        "p95": round(float(np.percentile(values, 95)), 4),
        "p97.5": round(float(np.percentile(values, 97.5)), 4),
    }


def run(
    trades: pd.DataFrame,
    start_capital: float,
    n_resamples: int = 10_000,
    mean_block: float = 10.0,
    seed: int = 42,
) -> MonteCarloResult:
    """Run both bootstraps and evaluate the SS7 CI gates."""
    profits = trades.sort_values("close_date")["profit_abs"].to_numpy(float)
    ratios = trades.sort_values("close_date")["profit_ratio"].to_numpy(float)
    n_trades = len(profits)
    result = MonteCarloResult(n_resamples=n_resamples, n_trades=n_trades)
    if n_trades < 10:
        logger.warning("only %d trades - Monte Carlo skipped", n_trades)
        result.gates["status"] = "insufficient trades (<10)"
        return result

    rng = np.random.default_rng(seed)
    span_days = max(
        (trades["close_date"].max() - trades["open_date"].min()).total_seconds()
        / 86400.0, 1.0,
    )
    trades_per_year = n_trades * 365.0 / span_days

    # --- iid bootstrap: expectancy / sharpe / profit factor ---
    iid_idx = rng.integers(0, n_trades, size=(n_resamples, n_trades))
    iid_profits = profits[iid_idx]
    iid_ratios = ratios[iid_idx]
    expectancy = iid_ratios.mean(axis=1)
    std = iid_ratios.std(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        sharpe = np.where(
            std > 0, iid_ratios.mean(axis=1) / std * math.sqrt(trades_per_year), 0.0
        )
    gains = np.where(iid_profits > 0, iid_profits, 0.0).sum(axis=1)
    losses = -np.where(iid_profits < 0, iid_profits, 0.0).sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        pf = np.where(losses > 0, gains / losses, np.inf)

    # --- stationary bootstrap: path metrics (max DD, CAGR) + streaks ---
    block_idx = _stationary_bootstrap_indices(n_trades, n_resamples, mean_block, rng)
    block_profits = profits[block_idx]
    equity = start_capital + np.cumsum(block_profits, axis=1)
    peaks = np.maximum.accumulate(np.maximum(equity, start_capital), axis=1)
    drawdowns = (peaks - equity) / peaks
    max_dd = drawdowns.max(axis=1)
    final = equity[:, -1]
    with np.errstate(invalid="ignore"):
        cagr = np.where(
            final > 0, (final / start_capital) ** (365.0 / span_days) - 1.0, -1.0
        )

    losing = block_profits < 0
    padded = np.concatenate(
        [np.zeros((n_resamples, 1), dtype=bool), losing], axis=1
    ).astype(np.int8)
    streaks = np.zeros(n_resamples, dtype=np.int64)
    current = np.zeros(n_resamples, dtype=np.int64)
    for col in range(1, padded.shape[1]):
        current = np.where(padded[:, col] == 1, current + 1, 0)
        streaks = np.maximum(streaks, current)

    result.ci = {
        "expectancy": _percentiles(expectancy),
        "sharpe": _percentiles(sharpe),
        "profit_factor": _percentiles(pf),
        "max_drawdown": _percentiles(max_dd),
        "cagr": _percentiles(cagr),
    }
    result.losing_streak = {
        "realized_max": int(_realized_streak(profits)),
        "mc_p95": int(np.percentile(streaks, 95)),
        "mc_p99": int(np.percentile(streaks, 99)),
        "mc_max": int(streaks.max()),
    }
    result.gates = evaluate_gates(result.ci)
    logger.info(
        "monte carlo: %d resamples, sharpe lo95=%.2f, maxDD hi95=%.1f%%",
        n_resamples, result.ci["sharpe"].get("p2.5", float("nan")),
        result.ci["max_drawdown"].get("p97.5", float("nan")) * 100,
    )
    return result


def _realized_streak(profits: np.ndarray) -> int:
    streak = worst = 0
    for value in profits:
        streak = streak + 1 if value < 0 else 0
        worst = max(worst, streak)
    return worst


def evaluate_gates(ci: dict) -> dict:
    """SS7 CI-bound gates: pass/fail per bound (95% CI)."""
    def lower(metric):
        return ci.get(metric, {}).get("p2.5")

    def upper(metric):
        return ci.get(metric, {}).get("p97.5")

    gates = {}
    checks = [
        ("sharpe_lower95_ge_0.7", lower("sharpe"), lambda v: v >= 0.7),
        ("expectancy_lower95_gt_0", lower("expectancy"), lambda v: v > 0),
        ("profit_factor_lower95_ge_1.15", lower("profit_factor"),
         lambda v: v >= 1.15),
        ("max_drawdown_upper95_le_0.30", upper("max_drawdown"),
         lambda v: v <= 0.30),
    ]
    for name, value, predicate in checks:
        gates[name] = {
            "value": value,
            "pass": bool(predicate(value)) if value is not None else None,
        }
    gates["all_pass"] = all(
        g["pass"] for g in gates.values() if isinstance(g, dict)
    )
    return gates
