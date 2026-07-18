"""Cross-sectional primitives - rank a metric across the universe, per date.

A cross-sectional strategy's signal for a stock depends on its RANK among all
peers on the same date, which the per-symbol ``entry_signal`` cannot see. These
helpers, called from ``prepare_cross_section`` (base.py), do the one thing every
such strategy needs: pivot a per-symbol metric to a date x symbol grid, rank it
row-wise (per date), and scatter a flag back into each symbol's frame.

Causality: each per-symbol metric is already computed from that symbol's own
lagged data, so ranking those as-of-date values across symbols introduces no
lookahead - the rank on date D uses only values known on D. Symbols not yet
listed on D are simply absent from that day's ranking (NaN), so the decile is
taken over the names actually trading that day.
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from algo.strategies.base import StrategyProfile
from algo.strategies.confidence import Component, ConfidenceScore, clip01, weighted

#: A date needs at least this many ranked names for a decile to be meaningful.
MIN_NAMES = 10


def align_metric(frames: Dict[str, pd.DataFrame], col: str) -> pd.DataFrame:
    """Wide frame: rows = sorted union of dates, columns = symbols, values =
    each symbol's ``col``. NaN where a symbol has no bar on that date."""
    series = {sym: df.set_index("date")[col]
              for sym, df in frames.items() if col in df.columns}
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).sort_index()


def _scatter(frames: Dict[str, pd.DataFrame], wide: pd.DataFrame,
             out_col: str) -> None:
    """Write a wide (date x symbol) frame back into each symbol's ``out_col``,
    aligned by date (in place)."""
    for sym, df in frames.items():
        if sym in wide.columns:
            df[out_col] = wide[sym].reindex(df["date"]).to_numpy()
        else:
            df[out_col] = np.nan


def decile_flag(frames: Dict[str, pd.DataFrame], metric_col: str,
                out_col: str, quantile: float = 0.10, top: bool = True) -> None:
    """Flag (1.0/0.0) whether each stock is in the favoured tail of ``metric_col``
    on each date. ``top`` picks the highest ``quantile`` (e.g. top decile),
    else the lowest. Dates with < MIN_NAMES ranked names produce no flag (0).
    Written into ``out_col`` on every frame, in place."""
    wide = align_metric(frames, metric_col)
    if wide.empty:
        for df in frames.values():
            df[out_col] = 0.0
        return
    valid = wide.count(axis=1) >= MIN_NAMES
    pct = wide.rank(axis=1, pct=True)                 # per-date percentile
    flag = (pct >= (1.0 - quantile)) if top else (pct <= quantile)
    flag = flag.where(valid, other=False).astype(float)
    _scatter(frames, flag, out_col)


def composite_percentile(frames: Dict[str, pd.DataFrame], cols, out_col: str,
                         ascending=None) -> None:
    """Average cross-sectional percentile of several metrics into ``out_col``.

    ``cols`` is a list of metric column names; ``ascending[i]`` False means a
    HIGHER value ranks better (default True for all). Used by factor composites.
    """
    ascending = ascending or [True] * len(cols)
    parts = []
    for col, asc in zip(cols, ascending):
        wide = align_metric(frames, col)
        if wide.empty:
            continue
        pct = wide.rank(axis=1, pct=True, ascending=asc)
        parts.append(pct)
    if not parts:
        for df in frames.values():
            df[out_col] = np.nan
        return
    combined = sum(parts) / len(parts)
    _scatter(frames, combined, out_col)


def market_return(frames: Dict[str, pd.DataFrame],
                  price_col: str = "close") -> pd.Series:
    """Equal-weight universe daily return by date (the 'market' proxy for
    residual-momentum and beta)."""
    wide = align_metric(frames, price_col)
    if wide.empty:
        return pd.Series(dtype=float)
    return wide.pct_change().mean(axis=1)


def breadth(frames: Dict[str, pd.DataFrame], cond_col: str) -> pd.Series:
    """Fraction of the universe for which ``cond_col`` (a 0/1 per-symbol flag) is
    true, by date - a market-breadth aggregate."""
    wide = align_metric(frames, cond_col)
    if wide.empty:
        return pd.Series(dtype=float)
    return wide.mean(axis=1)


def scatter_series(frames: Dict[str, pd.DataFrame], series: pd.Series,
                   out_col: str) -> None:
    """Broadcast one date-indexed market series into every frame's ``out_col``
    (in place), aligned by date - e.g. breadth or the market return."""
    for df in frames.values():
        df[out_col] = series.reindex(df["date"]).to_numpy() \
            if not series.empty else np.nan


def entered(flag: pd.Series) -> pd.Series:
    """Edge-trigger: True on the bar a stock ENTERS the favoured set (the flag
    goes 0->1), so one signal per entry rather than one per bar of membership."""
    f = flag.fillna(0.0) > 0.5
    return f & ~f.shift(1, fill_value=False)


class CrossSectionalDecileStrategy(StrategyProfile):
    """Base for a long-only decile sort: rank a per-symbol metric across the
    universe each day and enter a stock when it joins the favoured decile.

    A subclass sets ``meta`` and the class attributes below and implements
    ``compute_metric``; everything else (the two-phase prepare, the edge-trigger,
    a proximity-to-cutoff confidence) is shared, so the ten decile strategies do
    not each re-implement the same plumbing. NOT registered itself (no meta on
    this base); discovery only sees the concrete subclasses.
    """

    cross_sectional = True
    metric_col: str = "xs_metric"
    quantile: float = 0.10
    top: bool = True                    # True=highest decile, False=lowest

    def compute_metric(self, df: pd.DataFrame) -> pd.Series:
        """Per-symbol ranking metric (causal). Implemented by the subclass."""
        raise NotImplementedError

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out[self.metric_col] = self.compute_metric(out)
        return out

    def prepare_cross_section(self, frames):
        decile_flag(frames, self.metric_col, "xs_flag",
                    quantile=self.quantile, top=self.top)
        return frames

    def entry_signal(self, df: pd.DataFrame) -> pd.Series:
        if "xs_flag" not in df.columns or len(df) < self.min_history():
            return self.no_signal(df)
        return entered(df["xs_flag"]).fillna(False)

    def confidence(self, df: pd.DataFrame) -> ConfidenceScore:
        if df.empty or self.metric_col not in df.columns:
            return ConfidenceScore.zero("no metric")
        val = float(df[self.metric_col].iloc[-1]) \
            if df[self.metric_col].iloc[-1] == df[self.metric_col].iloc[-1] else 0.0
        return weighted(
            [Component("rank_metric", clip01(abs(val)), 1.0,
                       f"{self.metric_col}={val:.4g}")],
            reason=f"entered the {'top' if self.top else 'bottom'} "
                   f"{self.quantile:.0%} of {self.metric_col}")
