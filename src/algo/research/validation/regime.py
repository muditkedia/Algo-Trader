"""Objective market-regime labelling and per-regime metrics (SS11).

Implements the protocol's benchmark classifier - deliberately independent of
the strategy's signals and of algo_core's RegimeDetector (which it will later
be validated against, SS20):

Trend axis (daily):   Bull  = close > EMA50 AND EMA50 slope(10) > 0 AND ADX>=20
                      Bear  = close < EMA50 AND slope < 0 AND ADX >= 20
                      Range = otherwise (ADX < 20 or mixed direction)
Volatility axis:      vol_d = ATR%(14); trailing 365-day percentile rank:
                      High > 0.70, Low < 0.30, else Normal.

All computations are pandas-only (no TA-Lib dependency) and use trailing
data exclusively - identical to what a live classifier would see.
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from . import metrics as m
from .logging_utils import get_logger

logger = get_logger("regime")

TREND_LABELS = ("bull", "bear", "range")
VOL_LABELS = ("high_volatility", "normal_volatility", "low_volatility")
MIN_BUCKET_TRADES = 30  # SS11: ratios untrusted below this


# ------------------------------------------------------ indicators (pandas)

def _wilder_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def adx(daily: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ADX from daily OHLC (pandas implementation)."""
    high, low, close = daily["high"], daily["low"], daily["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0),
                        index=daily.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0),
                         index=daily.index)
    true_range = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = _wilder_ema(true_range, period)
    plus_di = 100 * _wilder_ema(plus_dm, period) / atr
    minus_di = 100 * _wilder_ema(minus_dm, period) / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return _wilder_ema(dx.fillna(0.0), period)


def atr_pct(daily: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = daily["high"], daily["low"], daily["close"]
    true_range = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return _wilder_ema(true_range, period) / close


def _trailing_percentile_rank(series: pd.Series, window: int = 365,
                              min_periods: int = 90) -> pd.Series:
    """Percentile rank of each value within its TRAILING window (no lookahead)."""
    def rank(values: np.ndarray) -> float:
        return float((values[:-1] <= values[-1]).mean())
    return series.rolling(window, min_periods=min_periods).apply(rank, raw=True)


# ------------------------------------------------------------------ labels

def label_daily(daily: pd.DataFrame, ema_period: int = 50,
                slope_lookback: int = 10, adx_floor: float = 20.0,
                vol_high: float = 0.70, vol_low: float = 0.30) -> pd.DataFrame:
    """Add trend_label and vol_label columns to a daily OHLCV frame.

    Requires columns date/open/high/low/close (date tz-aware or naive).
    """
    frame = daily.copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    frame = frame.sort_values("date").reset_index(drop=True)

    ema = frame["close"].ewm(span=ema_period, adjust=False).mean()
    slope = ema - ema.shift(slope_lookback)
    strength = adx(frame)

    bull = (frame["close"] > ema) & (slope > 0) & (strength >= adx_floor)
    bear = (frame["close"] < ema) & (slope < 0) & (strength >= adx_floor)
    frame["trend_label"] = np.select([bull, bear], ["bull", "bear"],
                                     default="range")

    vol = atr_pct(frame)
    vol_rank = _trailing_percentile_rank(vol)
    frame["vol_label"] = np.select(
        [vol_rank > vol_high, vol_rank < vol_low],
        ["high_volatility", "low_volatility"],
        default="normal_volatility",
    )
    frame.loc[vol_rank.isna(), "vol_label"] = "normal_volatility"
    return frame[["date", "trend_label", "vol_label"]]


def tag_trades(trades: pd.DataFrame,
               labels_by_pair: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Tag each trade with entry-day (primary) and exit-day regime labels.

    Trades on pairs without labels get 'unlabelled'. Adds a cross_regime
    flag when the entry and exit labels differ (SS11).
    """
    tagged = trades.copy()
    for prefix, date_col in (("entry", "open_date"), ("exit", "close_date")):
        trend, vol = [], []
        for row in tagged.itertuples(index=False):
            labels = labels_by_pair.get(row.pair)
            day = pd.Timestamp(getattr(row, date_col)).normalize()
            if labels is None:
                trend.append("unlabelled")
                vol.append("unlabelled")
                continue
            match = labels[labels["date"] <= day]
            if match.empty:
                trend.append("unlabelled")
                vol.append("unlabelled")
            else:
                trend.append(match.iloc[-1]["trend_label"])
                vol.append(match.iloc[-1]["vol_label"])
        tagged[f"{prefix}_trend_regime"] = trend
        tagged[f"{prefix}_vol_regime"] = vol
    tagged["cross_regime"] = (
        (tagged["entry_trend_regime"] != tagged["exit_trend_regime"])
        | (tagged["entry_vol_regime"] != tagged["exit_vol_regime"])
    )
    return tagged


def regime_breakdown(tagged: pd.DataFrame, start_capital: float) -> dict:
    """Per-regime metrics (entry-day labels) + the 3x3 trend x vol matrix."""
    if tagged.empty:
        return {"status": "no trades"}
    result: dict = {"by_trend": {}, "by_vol": {}, "matrix": {},
                    "cross_regime_share": round(
                        float(tagged["cross_regime"].mean()), 4)}
    for label, group in tagged.groupby("entry_trend_regime"):
        summary = m.summarize(group, start_capital)
        summary["trusted"] = bool(len(group) >= MIN_BUCKET_TRADES)
        result["by_trend"][str(label)] = summary
    for label, group in tagged.groupby("entry_vol_regime"):
        summary = m.summarize(group, start_capital)
        summary["trusted"] = bool(len(group) >= MIN_BUCKET_TRADES)
        result["by_vol"][str(label)] = summary
    for (trend, vol), group in tagged.groupby(
            ["entry_trend_regime", "entry_vol_regime"]):
        result["matrix"][f"{trend}|{vol}"] = {
            "trades": int(len(group)),
            "net_profit_abs": round(float(group["profit_abs"].sum()), 4),
            "profit_factor": round(m.profit_factor(group), 4),
            "win_rate": round(float((group["profit_abs"] > 0).mean()), 4),
            "median_holding_min": round(
                float(group["trade_duration"].median()), 2),
        }
    result["stability_gate"] = _stability_gate(result["by_trend"])
    return result


def _stability_gate(by_trend: dict) -> dict:
    """SS7 regime-stability gate: Bull profitable; no regime PF < 0.8."""
    bull = by_trend.get("bull", {})
    bull_ok = bool(bull.get("trades", 0) and bull.get("net_profit_abs", 0) > 0
                   and bull.get("profit_factor", 0) > 1.0)
    weak = {
        label: stats.get("profit_factor")
        for label, stats in by_trend.items()
        if stats.get("trades", 0) >= MIN_BUCKET_TRADES
        and isinstance(stats.get("profit_factor"), (int, float))
        and stats["profit_factor"] < 0.8
    }
    return {"bull_profitable": bull_ok, "regimes_below_pf_0.8": weak,
            "pass": bool(bull_ok and not weak)}
