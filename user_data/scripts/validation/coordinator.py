"""Validation coordinator - runs the validation modules in protocol order.

One entry point, ``run_validation``, executes the data-free pipeline over a
trades source (Freqtrade export path or an in-memory canonical frame):

    load -> metrics -> holding-time -> portfolio -> Monte Carlo -> stress
         -> regime (only when daily OHLCV is supplied) -> report

Sensitivity sweeps and walk-forward fold runs orchestrate MANY backtests, so
they are planned (not run) through their own modules; the coordinator accepts
their aggregated outputs and folds them into the report when present.

Every step logs through logging_utils (console + JSONL).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Union

import pandas as pd

from validation import loaders, metrics, monte_carlo, portfolio, regime, stress
from validation import report as report_mod
from validation.logging_utils import configure, get_logger, step

logger = get_logger("coordinator")


@dataclass
class ValidationOptions:
    start_capital: float = 1000.0
    max_open_trades: int = 3
    assumed_stop_pct: float = 0.06
    risk_per_trade: float = 0.01
    mc_resamples: int = 10_000
    mc_seed: int = 42
    report_title: str = "Backtest Validation Report"
    context_note: str = ""
    command: str = ""
    config_path: Optional[str] = None
    rejections_path: Optional[str] = None
    data_window: Optional[dict] = None
    gates: Optional[dict] = None
    daily_ohlcv: Optional[Dict[str, pd.DataFrame]] = None  # pair -> daily df
    sensitivity_result: Optional[dict] = None
    next_action: str = ""


def run_validation(
    trades_source: Union[str, Path, pd.DataFrame],
    out_report: Union[str, Path],
    options: Optional[ValidationOptions] = None,
    log_jsonl: Optional[Union[str, Path]] = None,
) -> dict:
    """Execute the full data-free validation pipeline; returns all results."""
    opts = options or ValidationOptions()
    if log_jsonl is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_jsonl = Path("user_data/logs") / f"validation_{stamp}.jsonl"
    configure(Path(log_jsonl))
    results: dict = {}

    with step(logger, "load_trades"):
        if isinstance(trades_source, (str, Path)):
            trades = loaders.load_backtest_export(trades_source)
        else:
            trades = loaders.ensure_trades(trades_source)
        results["n_trades"] = len(trades)

    with step(logger, "metrics", trades=len(trades)):
        results["metrics"] = metrics.summarize(trades, opts.start_capital)

    with step(logger, "holding_time"):
        results["holding"] = metrics.holding_time_stats(trades, by="exit_reason")

    with step(logger, "portfolio"):
        results["portfolio"] = portfolio.analyze(
            trades, opts.start_capital, opts.max_open_trades,
            assumed_stop_pct=opts.assumed_stop_pct,
            price_data=opts.daily_ohlcv,
        )

    with step(logger, "monte_carlo", resamples=opts.mc_resamples):
        mc_result = monte_carlo.run(
            trades, opts.start_capital,
            n_resamples=opts.mc_resamples, seed=opts.mc_seed,
        )
        results["monte_carlo"] = mc_result.as_dict()

    with step(logger, "stress"):
        results["stress"] = stress.run_all(
            trades, opts.start_capital, mc_result.losing_streak,
            risk_per_trade=opts.risk_per_trade,
            max_open_trades=opts.max_open_trades,
        )

    if opts.daily_ohlcv:
        with step(logger, "regime"):
            labels = {
                pair: regime.label_daily(frame)
                for pair, frame in opts.daily_ohlcv.items()
            }
            tagged = regime.tag_trades(trades, labels)
            results["regime"] = regime.regime_breakdown(
                tagged, opts.start_capital)
    else:
        results["regime"] = None
        logger.info("regime step skipped - no daily OHLCV supplied")

    with step(logger, "report"):
        config_snapshot = None
        if opts.config_path:
            try:
                full = loaders.load_freqtrade_config(opts.config_path)
                config_snapshot = {
                    "algo_trader": full.get("algo_trader", {}),
                    "stake_amount": full.get("stake_amount"),
                    "max_open_trades": full.get("max_open_trades"),
                    "dry_run": full.get("dry_run"),
                }
            except Exception as exc:
                logger.warning("config snapshot unavailable: %s", exc)
        rejections = (
            loaders.load_rejections(opts.rejections_path)
            if opts.rejections_path else None
        )
        equity = metrics.equity_curve(trades, opts.start_capital)
        inputs = report_mod.ReportInputs(
            title=opts.report_title,
            context_note=opts.context_note,
            command=opts.command,
            config_snapshot=config_snapshot,
            data_window=opts.data_window,
            gates=opts.gates,
            metrics=results["metrics"],
            portfolio=results["portfolio"],
            holding=results["holding"],
            monthly=metrics.monthly_returns(trades, opts.start_capital),
            drawdowns=metrics.top_drawdowns(equity),
            exit_reasons=_exit_reasons(trades),
            rejections=rejections,
            regime=results["regime"],
            sensitivity=opts.sensitivity_result,
            stress=results["stress"],
            mc=results["monte_carlo"],
            next_action=opts.next_action,
        )
        results["verdict"] = report_mod.compute_verdict(inputs)
        results["report_path"] = str(
            report_mod.generate(inputs, Path(out_report)))

    logger.info("validation complete: %s (verdict %s)",
                results["report_path"], results["verdict"]["status"])
    return results


def _exit_reasons(trades: pd.DataFrame) -> dict:
    if "exit_reason" not in trades.columns or trades.empty:
        return {}
    grouped = trades.groupby("exit_reason").agg(
        count=("profit_abs", "size"),
        net_profit=("profit_abs", "sum"),
        win_rate=("profit_abs", lambda s: float((s > 0).mean())),
        median_holding_min=("trade_duration", "median"),
    )
    return {
        str(reason): {k: round(float(v), 4) for k, v in row.items()}
        for reason, row in grouped.iterrows()
    }
