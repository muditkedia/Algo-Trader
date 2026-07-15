"""Stress tests (VALIDATION_RULES SS15).

Implemented now (pure post-processing of the trades frame - no market data):
* Double fees        - extra fee per side subtracted from every trade.
* Added slippage     - extra bps per side subtracted from every trade.
* Losing streaks     - realized + Monte-Carlo streak stress at full
                       concurrent exposure (risk-of-ruin check).

Defined now, executable after data exists:
* Volatility-spike windows - canonical shock windows returned as backtest
  timeranges (they are ordinary backtests on specific dates).
* Delayed exits      - requires candle data to reprice exits K candles late;
  the interface is fixed here and raises until price data is supplied.

Fee/slippage adjustments modify ``profit_ratio`` by the round-trip cost and
recompute ``profit_abs = profit_ratio * stake_amount``.
"""

from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from . import metrics as m
from .logging_utils import get_logger

logger = get_logger("stress")

#: Canonical shock windows (SS15) - ordinary backtests on these dates later.
SPIKE_WINDOWS = {
    "covid_crash_2020": "20200301-20200401",
    "may_2021_crash": "20210510-20210610",
    "ftx_collapse_2022": "20221101-20221201",
}


def _apply_cost(trades: pd.DataFrame, round_trip_cost: float) -> pd.DataFrame:
    adjusted = trades.copy()
    adjusted["profit_ratio"] = adjusted["profit_ratio"] - round_trip_cost
    adjusted["profit_abs"] = adjusted["profit_ratio"] * adjusted["stake_amount"]
    return adjusted


def with_extra_fees(trades: pd.DataFrame, extra_per_side: float = 0.001
                    ) -> pd.DataFrame:
    """Double-fee scenario: baseline already pays 0.1%/side; add the same again."""
    return _apply_cost(trades, 2.0 * extra_per_side)


def with_slippage(trades: pd.DataFrame, bps_per_side: float = 7.5
                  ) -> pd.DataFrame:
    """Execution-slippage scenario (default 7.5 bps/side, mid of 5-10)."""
    return _apply_cost(trades, 2.0 * bps_per_side / 10_000.0)


def delayed_exits(trades: pd.DataFrame,
                  price_data: Optional[Dict[str, pd.DataFrame]] = None,
                  delay_candles: int = 2) -> pd.DataFrame:
    """Reprice exits ``delay_candles`` 5m candles late (downtime scenario).

    Requires per-pair OHLCV; deliberately unavailable until the download
    phase. The signature is frozen now so the coordinator and report need no
    change later.
    """
    if not price_data:
        raise NotImplementedError(
            "delayed-exit stress requires candle data (post-download phase); "
            "see VALIDATION_RULES SS15."
        )
    raise NotImplementedError(
        "delayed-exit repricing is implemented in the data phase - "
        "kept as an explicit placeholder until OHLCV exists to verify against."
    )


def streak_stress(trades: pd.DataFrame, mc_losing_streak: dict,
                  risk_per_trade: float = 0.01, max_open_trades: int = 3,
                  max_dd_limit: float = 0.25) -> dict:
    """Consecutive-losing-streak stress (SS15, risk-of-ruin).

    Approximates the drawdown of the 99th-percentile Monte-Carlo streak at
    full concurrent exposure: streak_dd ~= streak_length * risk_per_trade,
    with all ``max_open_trades`` slots engaged.
    """
    realized = int(mc_losing_streak.get("realized_max", 0))
    p99 = int(mc_losing_streak.get("mc_p99", realized))
    stressed_dd = p99 * risk_per_trade
    concurrent_dd = stressed_dd + (max_open_trades - 1) * risk_per_trade
    return {
        "realized_max_streak": realized,
        "mc_p99_streak": p99,
        "implied_dd_at_p99": round(stressed_dd, 4),
        "implied_dd_with_full_concurrency": round(concurrent_dd, 4),
        "within_max_dd_limit": bool(concurrent_dd <= max_dd_limit),
    }


def run_all(trades: pd.DataFrame, start_capital: float,
            mc_losing_streak: dict, risk_per_trade: float = 0.01,
            max_open_trades: int = 3) -> dict:
    """Run every data-free stress scenario and evaluate SS15 pass criteria."""
    if trades.empty:
        return {"status": "no trades"}
    baseline = m.summarize(trades, start_capital)
    double_fees = m.summarize(with_extra_fees(trades), start_capital)
    slippage = m.summarize(with_slippage(trades), start_capital)

    def survives(stressed: dict) -> bool:
        return bool(
            stressed.get("net_profit_abs", 0) > 0
            and stressed.get("profit_factor", 0) >= 1.15
            and stressed.get("expectancy", 0) > 0
        )

    result = {
        "baseline": baseline,
        "double_fees": {**double_fees, "pass": survives(double_fees)},
        "slippage": {**slippage, "pass": survives(slippage)},
        "losing_streak": streak_stress(
            trades, mc_losing_streak, risk_per_trade, max_open_trades),
        "volatility_spike_windows": {
            "status": "requires historical data - backtest these timeranges "
                      "at the data phase",
            "windows": SPIKE_WINDOWS,
        },
        "delayed_exits": {
            "status": "requires candle data - interface frozen in stress.py",
        },
    }
    result["all_data_free_pass"] = bool(
        result["double_fees"]["pass"]
        and result["slippage"]["pass"]
        and result["losing_streak"]["within_max_dd_limit"]
    )
    logger.info("stress: double_fees=%s slippage=%s streak_ok=%s",
                result["double_fees"]["pass"], result["slippage"]["pass"],
                result["losing_streak"]["within_max_dd_limit"])
    return result
