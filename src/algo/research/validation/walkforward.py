"""Walk-forward framework (VALIDATION_RULES SS10 and SS10.1).

Provides, WITHOUT running anything:

* Fold-schedule generation for the three protocol cadences
  (monthly / quarterly / semi-annual), rolling IS + OOS windows with the
  1-month warmup pre-roll.
* Mode A (fixed-parameter) planning: one backtest command per OOS fold with
  the locked config.
* Mode A aggregation: stitch per-fold OOS trade frames into the
  concatenated OOS equity curve + per-fold metric table.
* Walk-forward efficiency (WFE) and the SS10.1 cadence-selection rule
  (parsimony: slowest cadence unless a faster one wins by the
  pre-registered margin).
* An ``Optimizer`` interface documenting the Mode B hook. Optimization is
  deliberately NOT implemented (protocol: separate approved task).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd
from dateutil.relativedelta import relativedelta

from . import metrics as m
from .logging_utils import get_logger

logger = get_logger("walkforward")

#: Protocol cadences: (is_months, oos_months, step_months). SS10.1.
CADENCES = {
    "monthly": (12, 1, 1),
    "quarterly": (12, 3, 3),
    "semiannual": (12, 6, 6),
}
WARMUP_MONTHS = 1  # data pre-roll so startup_candle_count is warm (SS3)


@dataclass(frozen=True)
class Fold:
    index: int
    warmup_start: datetime
    is_start: datetime
    is_end: datetime
    oos_start: datetime
    oos_end: datetime

    def timerange(self, segment: str) -> str:
        """Freqtrade --timerange string for 'is' or 'oos'."""
        fmt = "%Y%m%d"
        if segment == "is":
            return f"{self.is_start:{fmt}}-{self.is_end:{fmt}}"
        if segment == "oos":
            return f"{self.oos_start:{fmt}}-{self.oos_end:{fmt}}"
        raise ValueError(f"unknown segment {segment!r}")


def generate_folds(
    dev_start: str,
    dev_end: str,
    cadence: str = "quarterly",
) -> list:
    """Rolling folds over the development corpus for a protocol cadence.

    ``dev_start``/``dev_end``: ISO dates (e.g. "2020-01-01", "2025-06-30").
    The first OOS begins ``is_months`` after dev_start; folds advance by the
    cadence step until the OOS window would pass dev_end.
    """
    if cadence not in CADENCES:
        raise ValueError(f"unknown cadence {cadence!r}; use {sorted(CADENCES)}")
    is_months, oos_months, step_months = CADENCES[cadence]
    start = datetime.fromisoformat(dev_start)
    end = datetime.fromisoformat(dev_end)

    folds = []
    oos_start = start + relativedelta(months=is_months)
    index = 0
    while True:
        oos_end = oos_start + relativedelta(months=oos_months)
        if oos_end > end + relativedelta(days=1):
            break
        is_start = oos_start - relativedelta(months=is_months)
        folds.append(Fold(
            index=index,
            warmup_start=is_start - relativedelta(months=WARMUP_MONTHS),
            is_start=is_start,
            is_end=oos_start,
            oos_start=oos_start,
            oos_end=oos_end,
        ))
        oos_start += relativedelta(months=step_months)
        index += 1
    logger.info("%s cadence: %d folds (%s -> %s)", cadence, len(folds),
                dev_start, dev_end)
    return folds


def plan_mode_a(
    folds: list,
    base_config: str = "user_data/config.json",
    fee: float = 0.001,
    export_prefix: str = "user_data/backtest_results/wf",
) -> list:
    """One fixed-config backtest command per OOS fold. NOT executed here."""
    commands = []
    for fold in folds:
        export = f"{export_prefix}_fold{fold.index:02d}.json"
        commands.append(
            "docker compose run --rm freqtrade backtesting "
            f"--config {base_config} --strategy AdaptiveTrendStrategy "
            "--timeframe-detail 1m "
            f"--fee {fee} --timerange {fold.timerange('oos')} "
            f"--export trades --export-filename {export}"
        )
    logger.info("planned %d Mode A backtests (not executed)", len(commands))
    return commands


def aggregate_mode_a(
    fold_trades: list,
    start_capital: float,
) -> dict:
    """Stitch per-fold OOS trades into the aggregate OOS result.

    ``fold_trades``: list of canonical trade frames, one per fold (empty
    frames allowed - a fold may legitimately produce no trades).
    """
    per_fold = []
    non_empty = []
    for i, trades in enumerate(fold_trades):
        if trades is None or trades.empty:
            per_fold.append({"fold": i, "trades": 0})
            continue
        summary = m.summarize(trades, start_capital)
        summary["fold"] = i
        per_fold.append(summary)
        non_empty.append(trades)
    combined = (
        pd.concat(non_empty, ignore_index=True).sort_values("close_date")
        if non_empty else pd.DataFrame()
    )
    aggregate = m.summarize(combined, start_capital) if not combined.empty else {
        "trades": 0}
    logger.info("mode A aggregate: %d folds, %d OOS trades",
                len(fold_trades), aggregate.get("trades", 0))
    return {"per_fold": per_fold, "aggregate": aggregate,
            "combined_trades": combined}


def walk_forward_efficiency(is_return: float, oos_return: float) -> Optional[float]:
    """WFE = annualized OOS return / annualized IS return (SS9). None when
    the IS return is non-positive (ratio undefined)."""
    if is_return is None or oos_return is None or is_return <= 0:
        return None
    return round(oos_return / is_return, 4)


def score_cadences(results: dict, margin: float = 0.10) -> dict:
    """SS10.1 parsimony rule over per-cadence aggregate results.

    ``results``: cadence -> {"sharpe_lower_ci": float, "wfe": float|None,
                             "param_stability_ok": bool}
    Selection: the slowest cadence wins unless a faster one beats it by
    >= ``margin`` relative Sharpe at the lower CI AND has WFE >= 0.5 AND
    stable parameters. (Regime/pair consistency checks are applied by the
    caller before results enter this table.)
    """
    order = ["semiannual", "quarterly", "monthly"]  # slowest -> fastest
    available = [c for c in order if c in results]
    if not available:
        return {"selected": None, "reason": "no cadence results supplied"}
    selected = available[0]
    reasons = [f"prior: slowest available cadence '{selected}'"]
    for faster in available[1:]:
        fast, slow = results[faster], results[selected]
        fast_ok = (
            fast.get("wfe") is not None and fast["wfe"] >= 0.5
            and fast.get("param_stability_ok", False)
        )
        slow_sharpe = slow.get("sharpe_lower_ci") or 0.0
        fast_sharpe = fast.get("sharpe_lower_ci") or 0.0
        beats = (
            slow_sharpe <= 0 and fast_sharpe > 0
        ) or (
            slow_sharpe > 0 and fast_sharpe >= slow_sharpe * (1 + margin)
        )
        if fast_ok and beats:
            selected = faster
            reasons.append(
                f"'{faster}' beats '{available[0]}' by >= {margin:.0%} at the "
                f"lower CI with WFE >= 0.5 and stable params"
            )
    return {"selected": selected, "reasons": reasons, "inputs": results}


class Optimizer(ABC):
    """Mode B optimization hook (future; separate approved task).

    A Mode B implementation fits the pre-declared parameter subset on a
    fold's IS window and returns a config override for its OOS run. The
    framework contract is fixed now so Mode B needs no framework changes.
    """

    @abstractmethod
    def fit(self, fold: Fold) -> dict:
        """Return a config override (like sensitivity GridPoint.override)."""


class NotImplementedOptimizer(Optimizer):
    """Placeholder making the Mode B status explicit and testable."""

    def fit(self, fold: Fold) -> dict:
        raise NotImplementedError(
            "Mode B re-optimization is deliberately not implemented. "
            "Prerequisite per VALIDATION_RULES SS10: expose the pre-declared "
            "parameter subset (hyperopt params or config sweep) as a "
            "separate approved task."
        )
