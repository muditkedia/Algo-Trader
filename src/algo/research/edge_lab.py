"""Edge lab - promoted from archive/crypto-freqtrade/scripts/measure_entry_edge.py.

The L-009 methodology, verbatim in substance: a candidate is defined by ONLY
its entry rule, exits are ignored, and we measure what the entry itself is
worth against the cost hurdle.

  * forward returns at several horizons (gross),
  * MFE / MAE over the horizon window vs a random-entry baseline,
  * a DAY-CLUSTERED bootstrap CI on the mean (entries overlap, so an iid CI
    would lie - this was the single most important methodological point of the
    crypto post-mortem),
  * the verdict question: does the LOWER 95% bound of the gross mean exceed the
    round-trip cost? Not the point estimate - the bound.

The crypto entry passed "statistically significant" and still died, because
significance is not the bar: **beating costs** is (D-007). The D-007
implementation bar (gross edge >= 2x round-trip cost) is applied by the
research engine using these numbers.

Long horizons (D-028): resampling by single calendar day is the right
independence unit only while a signal's forward window fits inside one day.
At an 8-day horizon, signals four days apart share half their window - the
returns are mechanically autocorrelated across days, so an iid day resample
understates the variance of the mean and the CI comes out too NARROW, making
the D-007 gate too EASY. The fix resamples contiguous BLOCKS of days at least
as long as the horizon, via the stationary bootstrap already implemented in
validation.monte_carlo (Politis & Romano) - reused, not re-derived. Horizons
that fit within a day keep the original day resample bit-for-bit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

# The one existing stationary-bootstrap implementation in the project
# (VALIDATION_RULES SS14). Reusing its index generator is the point: one
# resampling algorithm, one set of properties, no drift.
from algo.research.validation.monte_carlo import _stationary_bootstrap_indices

DEFAULT_HORIZON_BARS = (1, 2, 4, 8)


@dataclass
class HorizonStats:
    bars: int
    minutes: float
    n: int
    gross_mean: float
    gross_median: float
    net_mean: float
    ci_low: float
    ci_high: float
    p_win: float
    random_mean: float
    #: Resampling block length (days) the CI used - 1 = plain day bootstrap,
    #: >1 = stationary block bootstrap (D-028). Recorded for auditability.
    ci_block_days: int = 1
    #: Selection edge = gross_mean - random_mean, i.e. the strategy's forward
    #: return in EXCESS of a random entry drawn from the same corpus (which
    #: earns the market drift). The block-bootstrap CI is on this DIFFERENCE.
    #: D-031: this is the drift-adjusted number the amended gate turns on -
    #: absolute return alone can be won by market participation (L-010).
    selection_mean: float = float("nan")
    selection_ci_low: float = float("nan")
    selection_ci_high: float = float("nan")

    @property
    def edge_vs_random_bps(self) -> float:
        return (self.gross_mean - self.random_mean) * 1e4

    @property
    def beats_cost(self) -> bool:
        """Is the LOWER bound of the GROSS mean above the cost hurdle?

        The pre-D-031 (absolute) test. Retained for continuity and reporting;
        the amended gate uses ``selection_beats_cost``.
        """
        return self.ci_low > self._cost

    @property
    def selection_beats_cost(self) -> bool:
        """Does the LOWER bound of the SELECTION edge clear the cost hurdle?

        The amended promotion test (D-031): the strategy must beat a random
        entry by more than it costs to trade - drift is not enough.
        """
        return self.selection_ci_low > self._cost

    _cost: float = 0.0


@dataclass
class EdgeReport:
    strategy: str
    n_signals: int
    cost_pct: float
    horizons: List[HorizonStats] = field(default_factory=list)
    mfe_mean: float = 0.0
    mae_mean: float = 0.0
    mfe_mae_ratio: float = 0.0
    random_mfe_mae_ratio: float = 0.0

    def best(self) -> Optional[HorizonStats]:
        """Horizon with the highest GROSS mean (absolute-return view)."""
        return max(self.horizons, key=lambda h: h.gross_mean) \
            if self.horizons else None

    def best_selection(self) -> Optional[HorizonStats]:
        """Horizon with the highest SELECTION edge (drift-adjusted view).

        The amended gate's horizon. Picking the best selection horizon cannot
        game drift - drift is already removed - and the identical rule applied
        to the random-entry control (which has no selection edge at ANY
        horizon) is what keeps the best-of-horizons choice honest (D-031).
        """
        scored = [h for h in self.horizons if h.selection_mean == h.selection_mean]
        return max(scored, key=lambda h: h.selection_mean) if scored else None

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy, "n_signals": self.n_signals,
            "cost_pct": round(self.cost_pct, 6),
            "mfe_mean": round(self.mfe_mean, 6),
            "mae_mean": round(self.mae_mean, 6),
            "mfe_mae_ratio": round(self.mfe_mae_ratio, 4),
            "random_mfe_mae_ratio": round(self.random_mfe_mae_ratio, 4),
            "horizons": [
                {"bars": h.bars, "minutes": h.minutes, "n": h.n,
                 "gross_mean_bps": round(h.gross_mean * 1e4, 2),
                 "net_mean_bps": round(h.net_mean * 1e4, 2),
                 "median_bps": round(h.gross_median * 1e4, 2),
                 "ci95_bps": [round(h.ci_low * 1e4, 2), round(h.ci_high * 1e4, 2)],
                 "ci_block_days": h.ci_block_days,
                 "p_win": round(h.p_win, 4),
                 "edge_vs_random_bps": round(h.edge_vs_random_bps, 2),
                 "selection_bps": round(h.selection_mean * 1e4, 2)
                 if h.selection_mean == h.selection_mean else None,
                 "selection_ci_bps": [
                     round(h.selection_ci_low * 1e4, 2)
                     if h.selection_ci_low == h.selection_ci_low else None,
                     round(h.selection_ci_high * 1e4, 2)
                     if h.selection_ci_high == h.selection_ci_high else None],
                 "beats_cost": h.beats_cost,
                 "selection_beats_cost": h.selection_beats_cost}
                for h in self.horizons],
        }


# --------------------------------------------------------------- primitives


def forward_returns(closes: np.ndarray, bars: int) -> np.ndarray:
    """Gross forward return ``bars`` ahead; NaN where the window runs out."""
    n = len(closes)
    out = np.full(n, np.nan)
    if bars < n:
        out[:n - bars] = closes[bars:] / closes[:n - bars] - 1.0
    return out


def mfe_mae(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
            window: int) -> tuple:
    """Max favourable / adverse excursion over the NEXT ``window`` bars
    (excludes the entry bar itself - archived convention)."""
    n = len(closes)
    mfe = np.full(n, np.nan)
    mae = np.full(n, np.nan)
    for i in range(n - window):
        hi = highs[i + 1:i + 1 + window]
        lo = lows[i + 1:i + 1 + window]
        mfe[i] = hi.max() / closes[i] - 1.0
        mae[i] = lo.min() / closes[i] - 1.0
    return mfe, mae


def day_bootstrap_ci(values: pd.Series, days: pd.Series,
                     n_boot: int = 2000, seed: int = 7) -> tuple:
    """95% CI of the mean, resampling by CALENDAR DAY.

    Signals cluster and overlap within a day, so days - not signals - are the
    independent unit. An iid bootstrap would understate the interval (L-009).
    Correct only while the forward window fits inside one day - for longer
    horizons use ``block_bootstrap_ci`` (D-028).
    """
    frame = pd.DataFrame({"v": values, "d": days}).dropna()
    if frame.empty:
        return (float("nan"), float("nan"))
    groups = [g["v"].to_numpy() for _, g in frame.groupby("d")]
    if len(groups) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot)
    for b in range(n_boot):
        pick = rng.integers(0, len(groups), len(groups))
        means[b] = np.concatenate([groups[i] for i in pick]).mean()
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def block_bootstrap_ci(values: pd.Series, days: pd.Series,
                       mean_block_days: int, n_boot: int = 2000,
                       seed: int = 7) -> tuple:
    """95% CI of the mean, resampling contiguous BLOCKS of calendar days.

    For a forward-return horizon spanning ``H`` days, signals on days closer
    than ``H`` apart share part of their window; day resampling treats them as
    independent and the CI comes out too narrow (the gate too easy - D-028).
    Resampling geometric-length blocks with mean ``mean_block_days`` keeps
    overlapping days together, so the between-block variance reflects the true
    number of independent observations.

    ``mean_block_days <= 1`` delegates to ``day_bootstrap_ci`` unchanged - the
    short-horizon path stays bit-identical to the L-009 methodology.
    """
    if mean_block_days <= 1:
        return day_bootstrap_ci(values, days, n_boot=n_boot, seed=seed)
    frame = pd.DataFrame({"v": values, "d": days}).dropna()
    if frame.empty:
        return (float("nan"), float("nan"))
    # groupby sorts keys, so the group sequence is chronological - required
    # for blocks of ADJACENT days to be meaningful.
    groups = [g["v"].to_numpy() for _, g in frame.groupby("d")]
    if len(groups) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    picks = _stationary_bootstrap_indices(
        len(groups), n_boot, float(mean_block_days), rng)
    means = np.empty(n_boot)
    for b in range(n_boot):
        means[b] = np.concatenate([groups[i] for i in picks[b]]).mean()
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def _block_means(values: pd.Series, days: pd.Series, mean_block_days: int,
                 n_boot: int, rng: np.random.Generator) -> Optional[np.ndarray]:
    """``n_boot`` resampled means of ``values``, blocked by day (D-028)."""
    frame = pd.DataFrame({"v": values, "d": days}).dropna()
    if frame.empty:
        return None
    groups = [g["v"].to_numpy() for _, g in frame.groupby("d")]
    if len(groups) < 2:
        return None
    block = max(1.0, float(mean_block_days))
    picks = _stationary_bootstrap_indices(len(groups), n_boot, block, rng)
    return np.array([np.concatenate([groups[i] for i in picks[b]]).mean()
                     for b in range(n_boot)])


def selection_diff_ci(signal_values: pd.Series, signal_days: pd.Series,
                      random_values: pd.Series, random_days: pd.Series,
                      mean_block_days: int, n_boot: int = 2000,
                      seed: int = 7) -> tuple:
    """95% CI of the SELECTION edge = mean(signal) - mean(random).

    Both samples are resampled by day-blocks (D-028) and INDEPENDENTLY (the
    random baseline is drawn from the whole corpus, so its sampling error is
    real and must widen the difference's interval, not be treated as a fixed
    constant). The paired difference of the two block-means gives an honest
    interval on the drift-adjusted edge (D-031). Deterministic under ``seed``;
    the signal and random resamples use separate, seed-derived generators so
    neither disturbs the other's stream.
    """
    rng_s = np.random.default_rng(seed)
    rng_r = np.random.default_rng(seed + 1)
    means_s = _block_means(signal_values, signal_days, mean_block_days,
                           n_boot, rng_s)
    means_r = _block_means(random_values, random_days, mean_block_days,
                           n_boot, rng_r)
    if means_s is None or means_r is None:
        return (float("nan"), float("nan"))
    diff = means_s - means_r
    return (float(np.percentile(diff, 2.5)), float(np.percentile(diff, 97.5)))


# ------------------------------------------------------------- measurement


def measure(
    frames: Dict[str, pd.DataFrame],
    signals: Dict[str, pd.Series],
    *,
    strategy: str,
    cost_pct: float,
    timeframe_minutes: float,
    horizon_bars: Sequence[int] = DEFAULT_HORIZON_BARS,
    seed: int = 7,
    bars_per_day: Optional[float] = None,
) -> EdgeReport:
    """Measure an entry rule's edge across symbols.

    ``frames``  symbol -> prepared OHLCV frame (with ``date``).
    ``signals`` symbol -> boolean Series aligned to that frame.

    ``bars_per_day`` (bars per trading session for this timeframe) activates
    the D-028 long-horizon CI: each horizon's resampling block is the number
    of days its forward window spans, ``ceil(bars / bars_per_day)``. Omitted
    (None), every horizon uses the plain day bootstrap - the pre-D-028
    behaviour, kept as the default so direct callers and stored comparisons
    are unaffected unless the caller opts in. The research engine always
    passes it.
    """
    max_h = max(horizon_bars)
    rows, random_rows = [], []
    rng = np.random.default_rng(seed)

    for symbol, frame in frames.items():
        signal = signals.get(symbol)
        if signal is None or frame.empty:
            continue
        closes = frame["close"].to_numpy(float)
        highs = frame["high"].to_numpy(float)
        lows = frame["low"].to_numpy(float)
        record = {f"r{b}": forward_returns(closes, b) for b in horizon_bars}
        mfe, mae = mfe_mae(highs, lows, closes, max_h)
        base = pd.DataFrame({**record, "mfe": mfe, "mae": mae,
                             "day": frame["date"].dt.normalize()})
        fired = signal.to_numpy(bool)
        rows.append(base[fired])
        # random baseline: same count of entries drawn from the same corpus
        valid = base.dropna(subset=[f"r{max_h}"])
        if len(valid) and fired.sum():
            take = min(len(valid), max(int(fired.sum()) * 5, 50))
            random_rows.append(valid.iloc[
                rng.choice(len(valid), size=take, replace=False)])

    entries = pd.concat(rows) if rows else pd.DataFrame()
    randoms = pd.concat(random_rows) if random_rows else pd.DataFrame()
    report = EdgeReport(strategy=strategy, n_signals=int(len(entries)),
                        cost_pct=cost_pct)
    if entries.empty:
        return report

    for bars in horizon_bars:
        col = f"r{bars}"
        values = entries[col]
        clean = values.dropna()
        if clean.empty:
            continue
        block_days = (int(math.ceil(bars / bars_per_day))
                      if bars_per_day else 1)
        lo, hi = block_bootstrap_ci(values, entries["day"], block_days,
                                    seed=seed)
        random_mean = float(randoms[col].dropna().mean()) \
            if not randoms.empty else float("nan")
        # Drift-adjusted (selection) edge and its difference CI (D-031).
        sel_mean = sel_lo = sel_hi = float("nan")
        if not randoms.empty:
            sel_mean = float(clean.mean()) - random_mean
            sel_lo, sel_hi = selection_diff_ci(
                values, entries["day"], randoms[col], randoms["day"],
                block_days, seed=seed)
        stats = HorizonStats(
            bars=bars, minutes=bars * timeframe_minutes, n=int(len(clean)),
            gross_mean=float(clean.mean()), gross_median=float(clean.median()),
            net_mean=float(clean.mean() - cost_pct),
            ci_low=lo, ci_high=hi,
            p_win=float((clean > cost_pct).mean()),
            random_mean=random_mean, ci_block_days=block_days,
            selection_mean=sel_mean, selection_ci_low=sel_lo,
            selection_ci_high=sel_hi)
        stats._cost = cost_pct
        report.horizons.append(stats)

    report.mfe_mean = float(entries["mfe"].dropna().mean() or 0.0)
    report.mae_mean = float(entries["mae"].dropna().mean() or 0.0)
    report.mfe_mae_ratio = (abs(report.mfe_mean / report.mae_mean)
                            if report.mae_mean else 0.0)
    if not randoms.empty:
        r_mfe = float(randoms["mfe"].dropna().mean() or 0.0)
        r_mae = float(randoms["mae"].dropna().mean() or 0.0)
        report.random_mfe_mae_ratio = abs(r_mfe / r_mae) if r_mae else 0.0
    return report
