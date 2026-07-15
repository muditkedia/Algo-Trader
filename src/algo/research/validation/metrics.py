"""Shared metric battery (VALIDATION_RULES SS6/SS7) and holding-time (SS12).

All functions take the canonical trades frame (see loaders.py) plus a
starting capital and return plain floats/dicts, so every other module can
reuse one implementation of each metric.

Methodology notes (documented per the protocol's reproducibility rules):
* Equity curve = start_capital + cumulative ``profit_abs`` ordered by close
  time (fixed-stake reality of the current config).
* Sharpe/Sortino are computed from DAILY-aggregated returns on start capital
  (missing days count as 0), annualized with sqrt(365) - crypto trades 24/7.
* Expectancy is the mean net ``profit_ratio`` per trade; the expectancy
  ratio is avg_win/|avg_loss| weighted by win rate (R-multiples need
  per-trade stop distances, which exports may not carry).

Market note (Phase 1): ``TRADING_DAYS_PER_YEAR`` defaults to 365 (24/7 markets).
For Indian equities the correct value is ~252, but changing it alone would be
inconsistent with ``daily_returns`` (which zero-fills every calendar day). The
consistent equity adaptation - session-aware daily returns keyed to the NSE
trading calendar plus 252-day annualization - is deliberately deferred to the
research-engine port (Phase 2), when a calendar is available and equity data
actually flows. The maths here is otherwise market-agnostic.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 365  # 24/7 default; equities override w/ calendar (Phase 2)


# ------------------------------------------------------------------ equity

def equity_curve(trades: pd.DataFrame, start_capital: float) -> pd.Series:
    """Equity after each closed trade, indexed by close time."""
    profits = trades.sort_values("close_date")
    return pd.Series(
        start_capital + profits["profit_abs"].cumsum().to_numpy(),
        index=profits["close_date"],
        name="equity",
    )


def daily_returns(trades: pd.DataFrame, start_capital: float) -> pd.Series:
    """Daily P&L as a fraction of start capital, zero-filled across the span."""
    if trades.empty:
        return pd.Series(dtype=float)
    daily = (
        trades.set_index("close_date")["profit_abs"]
        .resample("1D").sum()
        .fillna(0.0)
    )
    return daily / start_capital


def max_drawdown(equity: pd.Series) -> dict:
    """Max drawdown (fraction and absolute) plus its duration in days."""
    if equity.empty:
        return {"max_drawdown_pct": 0.0, "max_drawdown_abs": 0.0,
                "max_drawdown_days": 0.0}
    peak = equity.cummax()
    drawdown = (equity - peak) / peak
    trough_idx = int(np.argmin(drawdown.to_numpy()))
    dd_pct = float(-drawdown.iloc[trough_idx])
    dd_abs = float((peak - equity).iloc[trough_idx])
    # duration: time from the preceding peak to the trough
    peak_time = equity.index[: trough_idx + 1][
        (equity == peak).to_numpy()[: trough_idx + 1]
    ]
    duration_days = 0.0
    if len(peak_time):
        duration_days = (
            equity.index[trough_idx] - peak_time[-1]
        ).total_seconds() / 86400.0
    return {"max_drawdown_pct": dd_pct, "max_drawdown_abs": dd_abs,
            "max_drawdown_days": round(duration_days, 2)}


# ------------------------------------------------------------------ ratios

def _annualized(series: pd.Series, downside_only: bool = False) -> float:
    if len(series) < 2:
        return 0.0
    mean = series.mean()
    if downside_only:
        downside = series[series < 0]
        std = downside.std(ddof=1) if len(downside) > 1 else 0.0
    else:
        std = series.std(ddof=1)
    if not std or math.isnan(std):
        return 0.0
    return float(mean / std * math.sqrt(TRADING_DAYS_PER_YEAR))


def cagr(equity: pd.Series, start_capital: float) -> float:
    if equity.empty:
        return 0.0
    days = max(
        (equity.index[-1] - equity.index[0]).total_seconds() / 86400.0, 1.0
    )
    final = float(equity.iloc[-1])
    if final <= 0 or start_capital <= 0:
        return -1.0
    return float((final / start_capital) ** (365.0 / days) - 1.0)


def profit_factor(trades: pd.DataFrame) -> float:
    gross_profit = trades.loc[trades["profit_abs"] > 0, "profit_abs"].sum()
    gross_loss = -trades.loc[trades["profit_abs"] < 0, "profit_abs"].sum()
    if gross_loss <= 0:
        return float("inf") if gross_profit > 0 else 0.0
    return float(gross_profit / gross_loss)


def expectancy(trades: pd.DataFrame) -> dict:
    """Mean net profit_ratio per trade + classic expectancy ratio."""
    if trades.empty:
        return {"expectancy": 0.0, "expectancy_ratio": 0.0}
    wins = trades.loc[trades["profit_ratio"] > 0, "profit_ratio"]
    losses = trades.loc[trades["profit_ratio"] <= 0, "profit_ratio"]
    win_rate = len(wins) / len(trades)
    avg_win = wins.mean() if len(wins) else 0.0
    avg_loss = -losses.mean() if len(losses) else 0.0
    ratio = 0.0
    if avg_loss > 0:
        ratio = float((win_rate * avg_win / avg_loss) - (1 - win_rate))
    return {"expectancy": float(trades["profit_ratio"].mean()),
            "expectancy_ratio": ratio}


def max_consecutive_losses(trades: pd.DataFrame) -> int:
    streak = worst = 0
    for value in trades.sort_values("close_date")["profit_abs"]:
        streak = streak + 1 if value < 0 else 0
        worst = max(worst, streak)
    return worst


def quarterly_positive_share(trades: pd.DataFrame) -> float:
    """Share of calendar quarters with net-positive P&L."""
    if trades.empty:
        return 0.0
    quarterly = trades.set_index("close_date")["profit_abs"].resample("QE").sum()
    quarterly = quarterly[quarterly != 0.0] if len(quarterly) > 1 else quarterly
    if quarterly.empty:
        return 0.0
    return float((quarterly > 0).mean())


# ----------------------------------------------------------------- summary

def summarize(trades: pd.DataFrame, start_capital: float) -> dict:
    """The full SS6/SS7 metric battery as one flat dict."""
    if trades.empty:
        return {"trades": 0}
    equity = equity_curve(trades, start_capital)
    daily = daily_returns(trades, start_capital)
    dd = max_drawdown(equity)
    exp = expectancy(trades)
    growth = cagr(equity, start_capital)
    dd_pct = dd["max_drawdown_pct"]
    net_profit = float(trades["profit_abs"].sum())
    return {
        "trades": int(len(trades)),
        "net_profit_abs": round(net_profit, 4),
        "cagr": round(growth, 4),
        "sharpe": round(_annualized(daily), 4),
        "sortino": round(_annualized(daily, downside_only=True), 4),
        "profit_factor": round(profit_factor(trades), 4),
        "win_rate": round(float((trades["profit_abs"] > 0).mean()), 4),
        "expectancy": round(exp["expectancy"], 6),
        "expectancy_ratio": round(exp["expectancy_ratio"], 4),
        **{k: round(v, 4) for k, v in dd.items()},
        "recovery_factor": round(net_profit / dd["max_drawdown_abs"], 4)
        if dd["max_drawdown_abs"] > 0 else float("inf"),
        "calmar": round(growth / dd_pct, 4) if dd_pct > 0 else float("inf"),
        "quarterly_positive_share": round(quarterly_positive_share(trades), 4),
        "max_consecutive_losses": max_consecutive_losses(trades),
        "start": str(trades["open_date"].min()),
        "end": str(trades["close_date"].max()),
    }


# ------------------------------------------------------- holding time (SS12)

def holding_time_stats(trades: pd.DataFrame,
                       by: Optional[str] = None) -> dict:
    """Holding-time distribution per SS12 (minutes).

    ``by`` optionally breaks the stats down by a column (e.g. exit_reason).
    Includes the SS12 bimodality flag: substantial clusters both below 15min
    and above 180min indicate stop-out+trend-rider bimodality masquerading
    as a "one-hour average".
    """
    if trades.empty:
        return {"count": 0}
    durations = trades["trade_duration"].astype(float)
    stats = {
        "count": int(len(durations)),
        "mean": round(float(durations.mean()), 2),
        "median": round(float(durations.median()), 2),
        "p25": round(float(durations.quantile(0.25)), 2),
        "p75": round(float(durations.quantile(0.75)), 2),
        "p90": round(float(durations.quantile(0.90)), 2),
        "min": round(float(durations.min()), 2),
        "max": round(float(durations.max()), 2),
    }
    short_share = float((durations < 15).mean())
    long_share = float((durations > 180).mean())
    stats["bimodal_flag"] = bool(short_share > 0.20 and long_share > 0.20)
    stats["share_below_15m"] = round(short_share, 4)
    stats["share_above_180m"] = round(long_share, 4)
    stats["iqr_within_design_band"] = bool(
        stats["p25"] >= 20 and stats["p75"] <= 150
    )
    stats["median_within_design_band"] = bool(30 <= stats["median"] <= 120)
    if by and by in trades.columns:
        stats["breakdown"] = {
            str(key): holding_time_stats(group)
            for key, group in trades.groupby(by) if len(group)
        }
    return stats


def monthly_returns(trades: pd.DataFrame, start_capital: float) -> pd.Series:
    """Monthly net P&L as a fraction of start capital."""
    if trades.empty:
        return pd.Series(dtype=float)
    monthly = trades.set_index("close_date")["profit_abs"].resample("ME").sum()
    return (monthly / start_capital).round(6)


def top_drawdowns(equity: pd.Series, top_n: int = 5) -> list:
    """The ``top_n`` deepest drawdown episodes: depth, start, trough, recovery."""
    if equity.empty:
        return []
    peak = equity.cummax()
    dd = (equity - peak) / peak
    episodes, in_dd, start_idx = [], False, 0
    values = dd.to_numpy()
    for i, value in enumerate(values):
        if value < 0 and not in_dd:
            in_dd, start_idx = True, i
        elif value == 0 and in_dd:
            in_dd = False
            seg = dd.iloc[start_idx:i]
            episodes.append({
                "depth_pct": round(float(-seg.min()), 4),
                "start": str(dd.index[max(start_idx - 1, 0)]),
                "trough": str(seg.idxmin()),
                "recovered": str(dd.index[i]),
            })
    if in_dd:
        seg = dd.iloc[start_idx:]
        episodes.append({
            "depth_pct": round(float(-seg.min()), 4),
            "start": str(dd.index[max(start_idx - 1, 0)]),
            "trough": str(seg.idxmin()),
            "recovered": "ongoing",
        })
    return sorted(episodes, key=lambda e: -e["depth_pct"])[:top_n]
