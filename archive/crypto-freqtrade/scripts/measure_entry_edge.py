"""TEMPORARY Phase F: direct measurement of AdaptiveTrend's ENTRY edge (read-only).

Exits are ignored completely. For every entry the strategy would generate
(profile gate + full DecisionEngine GO, unconstrained by max_open_trades), we
measure the forward price behaviour:

  * MFE / MAE over the 180-min window
  * forward return at 30 / 60 / 90 / 120 / 180 min
  * full distribution: mean, median, percentiles
  * P(return exceeds 0.2% / 0.5% / 1% / 2%)   - before AND after fees
  * significance vs zero and vs a random-entry baseline, with a
    day-clustered bootstrap (entries overlap, so naive iid stats would lie)

Round-trip fee = 0.2% (0.1%/side, Binance spot taker).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append("user_data/scripts")
sys.path.append("user_data/strategies")

from freqtrade.strategy import merge_informative_pair

from algo_core.decision_engine import DecisionEngine, TradeContext
from algo_core.indicators import add_core_indicators
from algo_core.profiles.trend_following import TrendFollowingProfile
from algo_core.settings import AlgoSettings
from validation import loaders

DATA = Path("user_data/data/binance")
PAIRS = ["BTC/USDT", "ETH/USDT"]
START, END = "2020-01-01", "2025-06-30"
FEE = 0.002                       # round-trip
HORIZONS = {"30m": 6, "60m": 12, "90m": 18, "120m": 24, "180m": 36}
MAXH = 36
THRESHOLDS = [0.002, 0.005, 0.01, 0.02]
RNG = np.random.default_rng(7)

settings = AlgoSettings.from_config(
    loaders.load_freqtrade_config("user_data/config.json"))
IND = settings.indicators
engine = DecisionEngine(settings.engine, settings.risk)
profile = TrendFollowingProfile(settings=settings)

INFORMATIVE = [
    ("15m", IND.ema_15m_fast, IND.ema_15m_slow,
     ["date", "close", "ema_fast", "ema_slow", "adx", "rsi", "atr", "atr_pct",
      "swing_low", "structure_up"]),
    ("1h", IND.ema_1h_fast, IND.ema_1h_slow,
     ["date", "close", "ema_fast", "ema_slow", "adx", "closing_higher"]),
    ("4h", IND.ema_4h_fast, IND.ema_4h_slow,
     ["date", "close", "ema_fast", "ema_slow"]),
]


def _add(df, ef, es):
    return add_core_indicators(
        df, ema_fast=ef, ema_slow=es, adx_period=IND.adx_period,
        rsi_period=IND.rsi_period, atr_period=IND.atr_period,
        volume_window=IND.volume_window, structure_window=IND.structure_window,
        trend_shift=IND.trend_shift)


def build(pair: str) -> pd.DataFrame:
    def load(tf):
        d = pd.read_feather(DATA / f"{pair.replace('/', '_')}-{tf}.feather")
        d["date"] = pd.to_datetime(d["date"], utc=True)
        return d
    base = _add(load("5m"), IND.base_ema_fast, IND.base_ema_slow)
    for tf, ef, es, cols in INFORMATIVE:
        base = merge_informative_pair(base, _add(load(tf), ef, es)[cols],
                                      "5m", tf, ffill=True)
    base["date"] = pd.to_datetime(base["date"], utc=True)
    base = base.set_index("date").loc[START:END]

    c = base["close"].to_numpy(float)
    h = base["high"].to_numpy(float)
    lo = base["low"].to_numpy(float)
    n = len(c)
    for name, bars in HORIZONS.items():
        fwd = np.full(n, np.nan)
        fwd[:n - bars] = c[bars:] / c[:n - bars] - 1
        base[f"ret_{name}"] = fwd
    # MFE / MAE over the max horizon (excludes the entry bar itself)
    mfe = np.full(n, np.nan)
    mae = np.full(n, np.nan)
    for i in range(n - MAXH):
        w_h = h[i + 1:i + 1 + MAXH]
        w_l = lo[i + 1:i + 1 + MAXH]
        mfe[i] = w_h.max() / c[i] - 1
        mae[i] = w_l.min() / c[i] - 1
    base["mfe"] = mfe
    base["mae"] = mae
    return base


def entries(base: pd.DataFrame, pair: str) -> pd.DataFrame:
    """Candles where AdaptiveTrend would enter: profile gate + engine GO."""
    gate = profile.entry_signal(base)
    cand = base[gate.fillna(False)]
    keep = []
    for ts, row in cand.iterrows():
        d = engine.evaluate(TradeContext(pair, "long", row, 100.0, None, "trend"))
        if d.go:
            keep.append(ts)
    out = base.loc[keep].copy()
    out["pair"] = pair
    return out


def dist(x: np.ndarray, label: str, fee: float) -> dict:
    v = x[np.isfinite(x)] - fee
    return {
        "label": label, "n": len(v), "mean": v.mean(), "median": np.median(v),
        "p5": np.percentile(v, 5), "p25": np.percentile(v, 25),
        "p75": np.percentile(v, 75), "p95": np.percentile(v, 95),
        **{f"P>{t:.1%}": float((v > t).mean()) for t in THRESHOLDS},
    }


def day_bootstrap(df: pd.DataFrame, col: str, n_boot: int = 2000) -> tuple:
    """Mean CI clustered by calendar day (entries overlap -> iid CI would lie)."""
    d = df[[col]].dropna().copy()
    d["day"] = d.index.normalize()
    groups = [g[col].to_numpy() for _, g in d.groupby("day")]
    means = []
    for _ in range(n_boot):
        pick = RNG.integers(0, len(groups), len(groups))
        vals = np.concatenate([groups[i] for i in pick])
        means.append(vals.mean())
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def main() -> int:
    frames = {p: build(p) for p in PAIRS}
    ent = pd.concat([entries(frames[p], p) for p in PAIRS]).sort_index()
    print("=" * 92)
    print(f"ADAPTIVETREND ENTRY EDGE - {len(ent):,} entries "
          f"({START}..{END}, BTC+ETH, exits ignored)")
    print("=" * 92)

    # random baseline (same corpus, same count per pair)
    rnd = []
    for p in PAIRS:
        f = frames[p].dropna(subset=["ret_180m"])
        rnd.append(f.iloc[RNG.choice(len(f), size=min(20000, len(f)), replace=False)])
    rnd = pd.concat(rnd)

    print("\n[1] MFE / MAE over the 180-min window (before fees)")
    for c in ("mfe", "mae"):
        v = ent[c].dropna()
        r = rnd[c].dropna()
        print(f"  {c.upper():4} entry: mean={v.mean():+.4f} median={v.median():+.4f} "
              f"p25={v.quantile(.25):+.4f} p75={v.quantile(.75):+.4f}  |  "
              f"random mean={r.mean():+.4f}")
    print(f"  MFE/|MAE| ratio: entry={abs(ent['mfe'].mean()/ent['mae'].mean()):.2f}  "
          f"random={abs(rnd['mfe'].mean()/rnd['mae'].mean()):.2f}")

    print("\n[2] FORWARD RETURN DISTRIBUTION  (fees = 0.20% round trip)")
    hdr = (f"  {'horizon':8}{'fees':>7}{'mean':>10}{'median':>10}{'p5':>9}{'p25':>9}"
           f"{'p75':>9}{'p95':>9}" + "".join(f"{'P>'+f'{t:.1%}':>9}" for t in THRESHOLDS))
    print(hdr)
    for name in HORIZONS:
        for fee, tag in ((0.0, "gross"), (FEE, "net")):
            d = dist(ent[f"ret_{name}"].to_numpy(float), name, fee)
            print(f"  {name:8}{tag:>7}{d['mean']:>+10.5f}{d['median']:>+10.5f}"
                  f"{d['p5']:>+9.4f}{d['p25']:>+9.4f}{d['p75']:>+9.4f}{d['p95']:>+9.4f}"
                  + "".join(f"{d[f'P>{t:.1%}']:>9.1%}" for t in THRESHOLDS))

    print("\n[3] SIGNIFICANCE - mean forward return, day-clustered bootstrap 95% CI")
    print(f"  {'horizon':8}{'entry mean':>12}{'95% CI':>26}{'random mean':>13}"
          f"{'edge vs random':>16}{'beats fees?':>13}")
    for name in HORIZONS:
        col = f"ret_{name}"
        m = ent[col].mean()
        lo, hi = day_bootstrap(ent, col)
        rm = rnd[col].mean()
        beats = "YES" if lo > FEE else "NO"
        print(f"  {name:8}{m:>+12.5f}  [{lo:+.5f}, {hi:+.5f}]{rm:>+13.5f}"
              f"{(m-rm)*1e4:>+13.1f}bps{beats:>13}")
    print(f"\n  (fee hurdle = {FEE:.4f}. 'beats fees?' = is the LOWER 95% bound of the")
    print("   gross mean above the round-trip cost?)")

    ent[[c for c in ent.columns if c.startswith("ret_")] + ["mfe", "mae", "pair"]] \
        .to_csv("user_data/backtest_results/reports/entry_edge_data.csv")
    print("\ndata: user_data/backtest_results/reports/entry_edge_data.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
