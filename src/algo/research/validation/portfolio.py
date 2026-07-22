"""Portfolio-level validation battery (VALIDATION_RULES SS16).

Reconstructs the book of concurrent positions from the canonical trades
frame via an event sweep over open/close timestamps, then computes the nine
SS16 metrics with time-weighted averages.

Worst-case simultaneous-stop uses, per open trade, the recorded
``stop_distance_pct`` when present, else ``assumed_stop_pct`` (the 6% hard
cap) - both variants are reported so the naive bound is always visible.

Concurrent-holding correlation needs price history; when ``price_data`` is
not supplied (no market data downloaded yet), the module still reports which
pairs were co-held and for how long, and marks correlation as pending data.
"""

from __future__ import annotations

import math
from typing import Dict, Optional

import pandas as pd

from .logging_utils import get_logger
from . import metrics as m

logger = get_logger("portfolio")

MIN_CORR_POINTS = 30


def _events(trades: pd.DataFrame) -> pd.DataFrame:
    """Flatten trades into +open/-close events sorted by time."""
    stop = trades["stop_distance_pct"].astype(float)
    opens = pd.DataFrame({
        "time": trades["open_date"], "delta": 1, "pair": trades["pair"],
        "stake": trades["stake_amount"], "stop_pct": stop,
        "trade_id": trades.index,
    })
    closes = opens.copy()
    closes["time"] = trades["close_date"].to_numpy()
    closes["delta"] = -1
    events = pd.concat([opens, closes], ignore_index=True)
    # closes before opens at identical timestamps, so zero-duration overlap
    # is not double counted
    return events.sort_values(["time", "delta"]).reset_index(drop=True)


def analyze(
    trades: pd.DataFrame,
    start_capital: float,
    max_open_trades: int,
    assumed_stop_pct: float = 0.06,
    tradable_balance_ratio: float = 0.99,
    price_data: Optional[Dict[str, pd.DataFrame]] = None,
) -> dict:
    """Compute the SS16 portfolio battery. Returns one flat dict."""
    if trades.empty:
        return {"trades": 0}

    events = _events(trades)
    open_trades: dict = {}
    prev_time = None
    total_seconds = 0.0
    weighted_positions = 0.0
    weighted_exposure = 0.0
    max_positions = 0
    peak_exposure = 0.0
    worst_case_assumed = 0.0
    worst_case_recorded = 0.0
    pair_exposure_seconds: dict = {}
    pair_peak_exposure: dict = {}
    co_holding_seconds: dict = {}

    for row in events.itertuples(index=False):
        if prev_time is not None and open_trades:
            span = (row.time - prev_time).total_seconds()
            if span > 0:
                count = len(open_trades)
                exposure = sum(t["stake"] for t in open_trades.values())
                total_seconds += span
                weighted_positions += count * span
                weighted_exposure += exposure * span
                pairs_open = sorted({t["pair"] for t in open_trades.values()})
                for pair in pairs_open:
                    pair_exposure_seconds[pair] = (
                        pair_exposure_seconds.get(pair, 0.0)
                        + span * sum(t["stake"] for t in open_trades.values()
                                     if t["pair"] == pair)
                    )
                for i, pair_a in enumerate(pairs_open):
                    for pair_b in pairs_open[i + 1:]:
                        key = (pair_a, pair_b)
                        co_holding_seconds[key] = (
                            co_holding_seconds.get(key, 0.0) + span
                        )
        elif prev_time is None:
            prev_time = row.time

        if row.delta == 1:
            open_trades[row.trade_id] = {
                "pair": row.pair, "stake": row.stake, "stop_pct": row.stop_pct,
            }
        else:
            open_trades.pop(row.trade_id, None)

        # state-based extrema after applying the event
        if open_trades:
            count = len(open_trades)
            exposure = sum(t["stake"] for t in open_trades.values())
            max_positions = max(max_positions, count)
            peak_exposure = max(peak_exposure, exposure)
            assumed = sum(t["stake"] * assumed_stop_pct
                          for t in open_trades.values())
            recorded = sum(
                t["stake"] * (t["stop_pct"] if not math.isnan(t["stop_pct"])
                              else assumed_stop_pct)
                for t in open_trades.values()
            )
            worst_case_assumed = max(worst_case_assumed, assumed)
            worst_case_recorded = max(worst_case_recorded, recorded)
            for pair in {t["pair"] for t in open_trades.values()}:
                pair_exp = sum(t["stake"] for t in open_trades.values()
                               if t["pair"] == pair)
                pair_peak_exposure[pair] = max(
                    pair_peak_exposure.get(pair, 0.0), pair_exp
                )
        prev_time = row.time

    span_seconds = max(
        (trades["close_date"].max() - trades["open_date"].min()).total_seconds(),
        1.0,
    )
    avg_positions = weighted_positions / span_seconds
    avg_exposure = weighted_exposure / span_seconds
    utilization = avg_exposure / start_capital

    equity = m.equity_curve(trades, start_capital)
    dd = m.max_drawdown(equity)

    correlation = _co_holding_correlation(co_holding_seconds, price_data)

    result = {
        "trades": int(len(trades)),
        "avg_simultaneous_positions": round(avg_positions, 3),
        "max_simultaneous_positions": int(max_positions),
        "max_open_trades_config": int(max_open_trades),
        "max_positions_within_config": bool(max_positions <= max_open_trades),
        "peak_exposure_pct": round(peak_exposure / start_capital, 4),
        "peak_exposure_within_ratio": bool(
            peak_exposure / start_capital <= tradable_balance_ratio + 1e-9
        ),
        "avg_exposure_pct": round(avg_exposure / start_capital, 4),
        "capital_utilization_pct": round(utilization, 4),
        "idle_capital_pct": round(1.0 - utilization, 4),
        "utilization_in_band": bool(0.15 <= utilization <= 0.70),
        "exposure_by_asset_peak_pct": {
            pair: round(v / start_capital, 4)
            for pair, v in sorted(pair_peak_exposure.items())
        },
        "exposure_by_asset_time_weighted_pct": {
            pair: round(v / span_seconds / start_capital, 4)
            for pair, v in sorted(pair_exposure_seconds.items())
        },
        "portfolio_max_drawdown_pct": dd["max_drawdown_pct"],
        "worst_case_simultaneous_stop_pct_assumed": round(
            worst_case_assumed / start_capital, 4
        ),
        "worst_case_simultaneous_stop_pct_recorded": round(
            worst_case_recorded / start_capital, 4
        ),
        "worst_case_stop_within_15pct": bool(
            worst_case_assumed / start_capital <= 0.15
        ),
        "worst_case_stop_reject_over_20pct": bool(
            worst_case_assumed / start_capital > 0.20
        ),
        "co_holding": correlation,
    }
    logger.info(
        "portfolio: avg_pos=%.2f max_pos=%d util=%.1f%% worst_stop=%.1f%%",
        avg_positions, max_positions, utilization * 100,
        worst_case_assumed / start_capital * 100,
    )
    return result


def _co_holding_correlation(
    co_holding_seconds: dict,
    price_data: Optional[Dict[str, pd.DataFrame]],
) -> dict:
    """Correlation between simultaneously held assets (SS16 metric 8).

    With price data: correlation of aligned close-to-close returns per
    co-held pair combination (full-period; flagged as proxy when co-holding
    windows are too short to correlate alone). Without: overlap stats only.
    """
    overlap = {
        f"{a}|{b}": round(seconds / 3600.0, 2)
        for (a, b), seconds in sorted(co_holding_seconds.items())
    }
    if not price_data:
        return {
            "status": "pending price data - correlation requires OHLCV",
            "co_held_hours": overlap,
            "flagged_pairs": [],
        }
    correlations = {}
    flagged = []
    for key in overlap:
        pair_a, pair_b = key.split("|")
        if pair_a not in price_data or pair_b not in price_data:
            continue
        returns_a = price_data[pair_a]["close"].pct_change().dropna()
        returns_b = price_data[pair_b]["close"].pct_change().dropna()
        joined = pd.concat([returns_a, returns_b], axis=1, join="inner").dropna()
        if len(joined) < MIN_CORR_POINTS:
            correlations[key] = None
            continue
        corr = float(joined.corr().iloc[0, 1])
        correlations[key] = round(corr, 4)
        if corr > 0.7:
            flagged.append(key)
    return {
        "status": "computed (full-period returns as co-holding proxy)",
        "co_held_hours": overlap,
        "correlations": correlations,
        "flagged_pairs": flagged,
        "note": "persistent >0.7 correlation = illusory diversification",
    }
