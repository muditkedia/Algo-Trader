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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

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

    @property
    def edge_vs_random_bps(self) -> float:
        return (self.gross_mean - self.random_mean) * 1e4

    @property
    def beats_cost(self) -> bool:
        """Is the LOWER bound of the gross mean above the cost hurdle?"""
        return self.ci_low > self._cost

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
        return max(self.horizons, key=lambda h: h.gross_mean) \
            if self.horizons else None

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
                 "p_win": round(h.p_win, 4),
                 "edge_vs_random_bps": round(h.edge_vs_random_bps, 2),
                 "beats_cost": h.beats_cost}
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
) -> EdgeReport:
    """Measure an entry rule's edge across symbols.

    ``frames``  symbol -> prepared OHLCV frame (with ``date``).
    ``signals`` symbol -> boolean Series aligned to that frame.
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
        lo, hi = day_bootstrap_ci(values, entries["day"], seed=seed)
        stats = HorizonStats(
            bars=bars, minutes=bars * timeframe_minutes, n=int(len(clean)),
            gross_mean=float(clean.mean()), gross_median=float(clean.median()),
            net_mean=float(clean.mean() - cost_pct),
            ci_low=lo, ci_high=hi,
            p_win=float((clean > cost_pct).mean()),
            random_mean=float(randoms[col].dropna().mean())
            if not randoms.empty else float("nan"))
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
