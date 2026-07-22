"""Reusable causal context for 5-minute opening-session strategies."""

from __future__ import annotations

import numpy as np
import pandas as pd

from algo.core.indicators import adx, atr, ema, opening_range, session_vwap
from algo.strategies.cross_section import align_metric

IST = "Asia/Kolkata"


def local_dates(frame: pd.DataFrame) -> pd.Series:
    dates = pd.to_datetime(frame["date"])
    return dates.dt.tz_convert(IST) if dates.dt.tz is not None else dates


def prior_session_metrics(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series,
                                                          pd.Series, pd.Series]:
    """Prior close, ADT20, normalized opening gap, and daily NATR."""
    day = local_dates(frame).dt.normalize()
    work = frame.assign(_day=day.to_numpy())
    daily = work.groupby("_day").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), traded_value=("volume", "sum"))
    daily["traded_value"] *= daily["close"]
    prior_close = daily["close"].shift(1)
    adt20 = daily["traded_value"].shift(1).rolling(20, min_periods=20).mean()
    gap = daily["open"] / prior_close - 1.0
    gap_std = gap.shift(1).rolling(20, min_periods=20).std(ddof=0)
    gap_ratio = gap.abs() / gap_std.replace(0.0, np.nan)
    natr20 = (atr(daily, 14) / daily["close"] * 100.0).shift(1)
    return (day.map(prior_close), day.map(adt20), day.map(gap_ratio),
            day.map(natr20))


def completed_15m_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate completed groups of three 5-minute session bars causally."""
    if frame.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close",
                                     "volume"])
    raw = frame.copy().reset_index(drop=True)
    raw["_day"] = local_dates(raw).dt.normalize().to_numpy()
    raw["_bucket"] = raw.groupby("_day").cumcount() // 3
    bars = raw.groupby(["_day", "_bucket"], sort=True).agg(
        date=("date", "last"), open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"), volume=("volume", "sum"),
        count=("close", "size")).reset_index(drop=True)
    return bars[bars["count"] == 3].drop(columns="count")


def completed_15m_trend(frame: pd.DataFrame) -> pd.DataFrame:
    bars = completed_15m_bars(frame)
    if bars.empty:
        return pd.DataFrame(columns=["date", "nifty_trend"])
    bars["nifty_trend"] = np.where(
        ema(bars["close"], 9) > ema(bars["close"], 20), 1.0, -1.0)
    return bars[["date", "nifty_trend"]].sort_values("date")


def completed_15m_adx(frame: pd.DataFrame) -> pd.DataFrame:
    bars = completed_15m_bars(frame)
    if bars.empty:
        return pd.DataFrame(columns=["date", "adx15"])
    bars["adx15"] = adx(bars, 14)
    return bars[["date", "adx15"]].sort_values("date")


def add_opening_market_context(frames: dict, context: dict) -> dict:
    """Add NIFTY alignment and live-universe breadth to prepared frames."""
    if not frames:
        return frames
    nifty = context.get("NIFTY50", pd.DataFrame())
    nifty_exact = pd.DataFrame()
    if not nifty.empty:
        nifty_exact = nifty[["date", "open", "high", "low"]].copy().rename(
            columns={"open": "nifty_open", "high": "nifty_high",
                     "low": "nifty_low"})
        nifty_prior, _, _, _ = prior_session_metrics(nifty)
        nifty_day = local_dates(nifty).dt.normalize()
        nifty_session_open = nifty["open"].groupby(nifty_day).transform("first")
        nifty_exact["nifty_gap_pct"] = (
            nifty_session_open / nifty_prior - 1.0).to_numpy()
        nifty_exact["nifty_vwap"] = session_vwap(nifty)
        nifty_exact["nifty_close"] = nifty["close"].to_numpy()
        nifty_exact["nifty_ema20"] = ema(nifty["close"], 20).to_numpy()
        nifty_ib_high, nifty_ib_low, _ = opening_range(nifty, 30)
        nifty_exact["nifty_ib_high"] = nifty_ib_high.to_numpy()
        nifty_exact["nifty_ib_low"] = nifty_ib_low.to_numpy()
        nifty_or_high, nifty_or_low, _ = opening_range(nifty, 5)
        nifty_exact["nifty_or_high"] = nifty_or_high.to_numpy()
        nifty_exact["nifty_or_low"] = nifty_or_low.to_numpy()
        nifty_exact = nifty_exact.sort_values("date")
    trend = completed_15m_trend(nifty)

    close_wide = align_metric(frames, "close")
    prior_wide = align_metric(frames, "prior_close")
    vwap_wide = align_metric(frames, "vwap")
    if not close_wide.empty:
        advances = (close_wide > prior_wide).sum(axis=1)
        declines = (close_wide < prior_wide).sum(axis=1)
        ad_ratio = advances / declines.replace(0, np.nan)
        ad_ratio = ad_ratio.where(
            declines > 0, np.where(advances > 0, np.inf, np.nan))
        above_vwap = (close_wide > vwap_wide).mean(axis=1)
    else:
        ad_ratio = above_vwap = pd.Series(dtype=float)

    for df in frames.values():
        merged = df.sort_values("date").copy()
        if not nifty_exact.empty:
            merged = merged.merge(nifty_exact, on="date", how="left")
        else:
            merged["nifty_vwap"] = np.nan
            merged["nifty_close"] = np.nan
            merged["nifty_ema20"] = np.nan
            merged["nifty_open"] = np.nan
            merged["nifty_high"] = np.nan
            merged["nifty_low"] = np.nan
            merged["nifty_gap_pct"] = np.nan
            merged["nifty_ib_high"] = np.nan
            merged["nifty_ib_low"] = np.nan
            merged["nifty_or_high"] = np.nan
            merged["nifty_or_low"] = np.nan
        if not trend.empty:
            merged = pd.merge_asof(merged.sort_values("date"), trend,
                                   on="date", direction="backward")
        else:
            merged["nifty_trend"] = np.nan
        merged["ad_ratio"] = (ad_ratio.reindex(merged["date"]).to_numpy()
                              if not ad_ratio.empty else np.nan)
        merged["breadth_above_vwap"] = (
            above_vwap.reindex(merged["date"]).to_numpy()
            if not above_vwap.empty else np.nan)
        df.drop(columns=list(df.columns), inplace=True)
        for col in merged.columns:
            df[col] = merged[col].to_numpy()
    return frames
