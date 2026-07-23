"""Reusable causal context for 5-minute opening-session strategies."""

from __future__ import annotations

import numpy as np
import pandas as pd

from algo.core.indicators import (
    adx, atr, confirmed_fractal_pivots, donchian_channel, ema, opening_range,
    rolling_linear_channel, session_vwap, slot_relative_volume,
    swing_structure_bias,
)
from algo.strategies.cross_section import align_metric

IST = "Asia/Kolkata"


SHARED_5M_COLUMNS = frozenset({
    "atr", "ema9", "ema20", "vwap", "rvol", "prior_close", "adt20",
    "gap_ratio", "natr20", "bar_close_minute",
})

MARKET_CONTEXT_COLUMNS = frozenset({
    "nifty_open", "nifty_high", "nifty_low", "nifty_close", "nifty_vwap",
    "nifty_ema20", "nifty_gap_pct", "nifty_channel_slope_pct",
    "nifty_ib_high", "nifty_ib_low", "nifty_or_high", "nifty_or_low",
    "nifty_trend", "ad_ratio", "breadth_above_vwap",
    "nifty_dc_upper20", "nifty_dc_lower20", "nifty_dc_upper10",
    "nifty_dc_lower10", "nifty_structure",
})


def local_dates(frame: pd.DataFrame) -> pd.Series:
    dates = pd.to_datetime(frame["date"])
    return dates.dt.tz_convert(IST) if dates.dt.tz is not None else dates


def session_linear_channel(frame: pd.DataFrame, lookback: int = 20,
                           std_mult: float = 2.0) -> pd.DataFrame:
    """Apply the shared prior-only OLS channel independently per session."""
    columns = ["baseline", "slope_pct", "r2", "resid_std", "upper", "lower"]
    out = pd.DataFrame(np.nan, index=frame.index, columns=columns)
    day = local_dates(frame).dt.normalize()
    for _, indices in day.groupby(day).groups.items():
        channel = rolling_linear_channel(
            frame.loc[indices, "close"], lookback, std_mult)
        out.loc[indices, columns] = channel[columns].to_numpy()
    return out


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


def shared_5m_features(frame: pd.DataFrame, atr_period: int = 14,
                       rvol_sessions: int = 10) -> pd.DataFrame:
    """Copy a 5-minute frame and ensure its common causal features exist.

    The production orchestrator calls this once per symbol and shares the
    result with every 5-minute strategy.  Strategy ``prepare`` methods also
    call it, which preserves standalone/backtest behaviour: a raw frame is
    enriched locally, while an orchestrator-enriched frame is only copied.
    No global cache is involved, so a completed-candle update can never reuse
    stale values.
    """
    df = frame.copy().reset_index(drop=True)
    if "atr" not in df:
        df["atr"] = atr(df, atr_period)
    if "ema9" not in df:
        df["ema9"] = ema(df["close"], 9)
    if "ema20" not in df:
        df["ema20"] = ema(df["close"], 20)
    if "vwap" not in df:
        df["vwap"] = session_vwap(df)
    if "rvol" not in df:
        df["rvol"] = slot_relative_volume(df, rvol_sessions)
    missing_daily = {"prior_close", "adt20", "gap_ratio", "natr20"} \
        - set(df.columns)
    if missing_daily:
        prior_close, adt20, gap_ratio, natr20 = prior_session_metrics(df)
        values = {"prior_close": prior_close, "adt20": adt20,
                  "gap_ratio": gap_ratio, "natr20": natr20}
        for column in missing_daily:
            df[column] = values[column]
    if "bar_close_minute" not in df:
        local = local_dates(df)
        df["bar_close_minute"] = local.dt.hour * 60 + local.dt.minute + 5
    return df


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


def completed_15m_channel_slope(frame: pd.DataFrame,
                                lookback: int = 20) -> pd.DataFrame:
    """Prior-only 15-minute regression slope, aligned at completed bars."""
    bars = completed_15m_bars(frame)
    if bars.empty:
        return pd.DataFrame(columns=["date", "channel_slope_15m"])
    bars["channel_slope_15m"] = rolling_linear_channel(
        bars["close"], lookback, 2.0)["slope_pct"]
    return bars[["date", "channel_slope_15m"]].sort_values("date")


def completed_15m_structure(frame: pd.DataFrame, k: int = 2) -> pd.DataFrame:
    """Confirmed 15-minute HH/HL or LH/LL bias, aligned at completed bars."""
    bars = completed_15m_bars(frame)
    if bars.empty:
        return pd.DataFrame(columns=["date", "structure_15m"])
    local = local_dates(bars)
    pivot_high, pivot_low = confirmed_fractal_pivots(
        bars, k=k, groups=local.dt.normalize())
    bars["structure_15m"] = swing_structure_bias(
        bars, k=k, groups=local.dt.normalize(), pivot_high=pivot_high,
        pivot_low=pivot_low)
    return bars[["date", "structure_15m"]].sort_values("date")


def add_opening_market_context(frames: dict, context: dict,
                               *, inplace: bool = True) -> dict:
    """Add NIFTY alignment and live-universe breadth to prepared frames.

    Standalone strategy callers historically receive in-place enrichment.
    The orchestrator owns its per-scan frame mapping and requests replacement
    instead, avoiding fragmented multi-column mutation on every symbol.
    """
    if not frames:
        return frames
    # The orchestrator computes this cross-sectional block once per completed
    # 5-minute scan.  Per-strategy calls remain for standalone/backtest use,
    # but become an O(1) no-op when the shared columns are already present.
    if all(MARKET_CONTEXT_COLUMNS.issubset(df.columns)
           for df in frames.values()):
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
        nifty_exact["nifty_channel_slope_pct"] = session_linear_channel(
            nifty, 20, 2.0)["slope_pct"].to_numpy()
        nifty_ib_high, nifty_ib_low, _ = opening_range(nifty, 30)
        nifty_exact["nifty_ib_high"] = nifty_ib_high.to_numpy()
        nifty_exact["nifty_ib_low"] = nifty_ib_low.to_numpy()
        nifty_or_high, nifty_or_low, _ = opening_range(nifty, 5)
        nifty_exact["nifty_or_high"] = nifty_or_high.to_numpy()
        nifty_exact["nifty_or_low"] = nifty_or_low.to_numpy()
        nifty_upper20, nifty_lower20, _ = donchian_channel(
            nifty, 20, nifty_day)
        nifty_upper10, nifty_lower10, _ = donchian_channel(
            nifty, 10, nifty_day)
        nifty_exact["nifty_dc_upper20"] = nifty_upper20.to_numpy()
        nifty_exact["nifty_dc_lower20"] = nifty_lower20.to_numpy()
        nifty_exact["nifty_dc_upper10"] = nifty_upper10.to_numpy()
        nifty_exact["nifty_dc_lower10"] = nifty_lower10.to_numpy()
        nifty_exact["nifty_structure"] = swing_structure_bias(
            nifty, 2, nifty_day).to_numpy()
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

    for symbol, df in list(frames.items()):
        merged = df.sort_values("date").copy()
        if not nifty_exact.empty:
            merged = merged.merge(nifty_exact, on="date", how="left")
        else:
            merged["nifty_vwap"] = np.nan
            merged["nifty_close"] = np.nan
            merged["nifty_ema20"] = np.nan
            merged["nifty_channel_slope_pct"] = np.nan
            merged["nifty_open"] = np.nan
            merged["nifty_high"] = np.nan
            merged["nifty_low"] = np.nan
            merged["nifty_gap_pct"] = np.nan
            merged["nifty_ib_high"] = np.nan
            merged["nifty_ib_low"] = np.nan
            merged["nifty_or_high"] = np.nan
            merged["nifty_or_low"] = np.nan
            merged["nifty_dc_upper20"] = np.nan
            merged["nifty_dc_lower20"] = np.nan
            merged["nifty_dc_upper10"] = np.nan
            merged["nifty_dc_lower10"] = np.nan
            merged["nifty_structure"] = np.nan
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
        merged = merged.reset_index(drop=True)
        if inplace:
            # ``prepare_context`` historically enriched the caller's prepared
            # frame object. Keep that contract for standalone users.
            context_columns = sorted(MARKET_CONTEXT_COLUMNS)
            df[context_columns] = merged[context_columns].to_numpy()
            frames[symbol] = df
        else:
            frames[symbol] = merged
    return frames
