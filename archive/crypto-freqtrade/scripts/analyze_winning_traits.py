"""TEMPORARY Phase E analysis: winning-trade characteristic discovery.

Reuses the Phase C export + Phase B data + Phase D per-trade CSV. Computes an
extended entry-time feature set for all 890 trades, ranks every feature by
predictive value using several methods (univariate AUC, Spearman corr with
profit, KS test, quintile monotonicity/lift), and benchmarks actual entries
against RANDOM entries at fixed horizons to answer: is there any entry edge?

READ-ONLY: changes no strategy/tooling state; writes one CSV + prints report.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append("user_data/scripts")
sys.path.append("user_data/strategies")

from freqtrade.strategy import merge_informative_pair

from algo_core.indicators import add_core_indicators
from algo_core.settings import AlgoSettings
from validation import loaders

try:
    from scipy import stats as sps
    HAVE_SCIPY = True
except ImportError:
    HAVE_SCIPY = False

DATA = Path("user_data/data/binance")
PAIRS = ["BTC/USDT", "ETH/USDT"]
ZIP = "user_data/backtest_results/backtest-result-2026-07-14_09-19-41.zip"
D_CSV = Path("user_data/backtest_results/reports/root_cause_data.csv")
RNG = np.random.default_rng(42)

settings = AlgoSettings.from_config(
    loaders.load_freqtrade_config("user_data/config.json"))
IND = settings.indicators


def _add(df, ef, es):
    return add_core_indicators(
        df, ema_fast=ef, ema_slow=es, adx_period=IND.adx_period,
        rsi_period=IND.rsi_period, atr_period=IND.atr_period,
        volume_window=IND.volume_window, structure_window=IND.structure_window,
        trend_shift=IND.trend_shift)


def build_frame(pair: str) -> pd.DataFrame:
    """5m frame with informative merges + extended entry-time features."""
    base = _add(pd.read_feather(DATA / f"{pair.replace('/', '_')}-5m.feather"),
                IND.base_ema_fast, IND.base_ema_slow)

    inf15 = _add(pd.read_feather(DATA / f"{pair.replace('/', '_')}-15m.feather"),
                 IND.ema_15m_fast, IND.ema_15m_slow)
    inf1h = _add(pd.read_feather(DATA / f"{pair.replace('/', '_')}-1h.feather"),
                 IND.ema_1h_fast, IND.ema_1h_slow)
    inf4h = _add(pd.read_feather(DATA / f"{pair.replace('/', '_')}-4h.feather"),
                 IND.ema_4h_fast, IND.ema_4h_slow)

    # trend maturity on 1h: consecutive hours with fast EMA above slow EMA
    aligned = inf1h["ema_fast"] > inf1h["ema_slow"]
    inf1h["trend_age"] = aligned.groupby((~aligned).cumsum()).cumcount()
    # 4h headroom: distance to the rolling 30-bar (5-day) high
    inf4h["high_5d"] = inf4h["high"].rolling(30).max()

    base = merge_informative_pair(
        base, inf15[["date", "close", "ema_fast", "ema_slow", "adx", "rsi",
                     "atr", "atr_pct", "swing_low", "structure_up",
                     "volume_ratio"]], "5m", "15m", ffill=True)
    base = merge_informative_pair(
        base, inf1h[["date", "close", "ema_fast", "ema_slow", "adx",
                     "closing_higher", "trend_age"]], "5m", "1h", ffill=True)
    base = merge_informative_pair(
        base, inf4h[["date", "close", "ema_fast", "ema_slow", "adx",
                     "high_5d"]], "5m", "4h", ffill=True)

    c = base["close"]
    base["ema_sep_5m"] = (base["ema_fast"] - base["ema_slow"]) / c
    base["ema_sep_15m"] = (base["ema_fast_15m"] - base["ema_slow_15m"]) / c
    base["ema_sep_1h"] = (base["ema_fast_1h"] - base["ema_slow_1h"]) / c
    base["ema_sep_4h"] = (base["ema_fast_4h"] - base["ema_slow_4h"]) / c
    base["dist_fast_5m"] = (c - base["ema_fast"]) / c
    base["ext_1h_ema"] = (c - base["ema_fast_1h"]) / c
    base["pullback_3h"] = (c.rolling(36).max() - c) / c
    base["headroom_5d"] = (base["high_5d_4h"] - c) / c
    base["breakout_24h"] = (c > c.shift(1).rolling(288).max()).astype(float)
    base["mom_1h"] = c / c.shift(12) - 1
    base["mom_accel"] = base["mom_1h"] - base["mom_1h"].shift(12)
    base["ret_24h"] = c / c.shift(288) - 1
    base["atr_expansion"] = base["atr_pct_15m"] / base["atr_pct_15m"].shift(48)
    rng_ = (base["high"] - base["low"]).replace(0, np.nan)
    base["body_ratio"] = (base["close"] - base["open"]).abs() / rng_
    base["upper_wick"] = (base["high"] - base[["open", "close"]].max(axis=1)) / rng_
    base["hour"] = pd.to_datetime(base["date"], utc=True).dt.hour

    # forward outcomes for the random-entry benchmark (future data, outcome only)
    for h, bars in (("1h", 12), ("3h", 36), ("6h", 72)):
        base[f"fwd_ret_{h}"] = c.shift(-bars) / c - 1
        base[f"fwd_mfe_{h}"] = base["high"].rolling(bars).max().shift(-bars) / c - 1

    base["date"] = pd.to_datetime(base["date"], utc=True)
    return base.set_index("date")


FEATURES = [
    "score", "adx_1h", "adx_15m", "adx_4h", "rsi_15m", "volume_ratio",
    "volume_ratio_15m", "atr_pct_15m", "rr",
    "ema_sep_5m", "ema_sep_15m", "ema_sep_1h", "ema_sep_4h",
    "dist_fast_5m", "ext_1h_ema", "pullback_3h", "headroom_5d",
    "breakout_24h", "mom_1h", "mom_accel", "ret_24h", "atr_expansion",
    "body_ratio", "upper_wick", "hour", "trend_age_1h", "duration",
]


def build_dataset() -> tuple:
    trades = loaders.load_backtest_export(ZIP)
    d_csv = pd.read_csv(D_CSV)
    assert len(d_csv) == len(trades)
    # positional join with fidelity check against Phase D output
    assert np.allclose(d_csv["profit_abs"].to_numpy(),
                       trades["profit_abs"].to_numpy(), atol=1e-6)

    frames = {p: build_frame(p) for p in PAIRS}
    rows = []
    for i, t in enumerate(trades.itertuples()):
        frame = frames[t.pair]
        pos = int(frame.index.searchsorted(t.open_date))
        row = frame.iloc[max(pos - 1, 0)]
        rec = {f: row.get(f) for f in FEATURES if f in frame.columns}
        rec.update({
            "pair": t.pair, "open_date": t.open_date,
            "win": bool(t.profit_abs > 0),
            "profit_ratio": float(t.profit_ratio),
            "duration": float(t.trade_duration),
            "exit_reason": t.exit_reason,
            # carried from Phase D (same trade order, verified above)
            "score": d_csv.iloc[i]["score"], "rr": d_csv.iloc[i]["rr"],
            "mfe": d_csv.iloc[i]["mfe"], "mae": d_csv.iloc[i]["mae"],
            "regime": d_csv.iloc[i]["regime"],
            "fwd_ret_1h": row.get("fwd_ret_1h"),
            "fwd_ret_3h": row.get("fwd_ret_3h"),
            "fwd_ret_6h": row.get("fwd_ret_6h"),
            "fwd_mfe_3h": row.get("fwd_mfe_3h"),
        })
        rows.append(rec)
    return pd.DataFrame(rows), frames


def auc(feature: pd.Series, target: pd.Series) -> float:
    """Univariate ROC-AUC via the rank/Mann-Whitney identity."""
    sub = pd.concat([feature, target], axis=1).dropna()
    x, y = sub.iloc[:, 0], sub.iloc[:, 1].astype(bool)
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return np.nan
    ranks = x.rank()
    return float((ranks[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def feature_report(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for f in FEATURES:
        if f not in df.columns or df[f].dropna().nunique() < 3:
            continue
        sub = df[[f, "win", "profit_ratio"]].dropna()
        a = auc(sub[f], sub["win"])
        rho = sub[f].corr(sub["profit_ratio"], method="spearman")
        ks_p = np.nan
        if HAVE_SCIPY:
            w, l = sub.loc[sub["win"], f], sub.loc[~sub["win"], f]
            if len(w) > 10 and len(l) > 10:
                ks_p = sps.ks_2samp(w, l).pvalue
        # quintile lift on mean profit
        try:
            q = pd.qcut(sub[f], 5, labels=False, duplicates="drop")
            prof = sub.groupby(q)["profit_ratio"].mean()
            lift = float(prof.iloc[-1] - prof.iloc[0])
            mono = float(prof.corr(pd.Series(range(len(prof))), method="spearman"))
        except (ValueError, IndexError):
            lift, mono = np.nan, np.nan
        out.append({"feature": f, "auc_win": round(a, 3),
                    "spearman_profit": round(rho, 3),
                    "ks_pvalue": None if np.isnan(ks_p) else round(ks_p, 4),
                    "q5_minus_q1_profit": None if np.isnan(lift) else round(lift, 4),
                    "quintile_monotonicity": None if np.isnan(mono) else round(mono, 2)})
    rep = pd.DataFrame(out)
    rep["strength"] = (rep["auc_win"] - 0.5).abs()
    return rep.sort_values("strength", ascending=False).drop(columns="strength")


def quintile_detail(df: pd.DataFrame, feature: str) -> None:
    sub = df[[feature, "win", "profit_ratio", "mfe"]].dropna()
    q = pd.qcut(sub[feature], 5, duplicates="drop")
    g = sub.groupby(q, observed=True).agg(
        n=("win", "size"), win=("win", "mean"),
        mean_profit=("profit_ratio", "mean"), mean_mfe=("mfe", "mean"))
    print(f"\n  {feature} quintiles:")
    for k, r in g.iterrows():
        print(f"    {str(k):28} n={int(r['n']):3} win={r['win']:.1%} "
              f"profit={r['mean_profit']:+.4f} mfe={r['mean_mfe']:+.4f}")


def entry_edge(df: pd.DataFrame, frames: dict) -> None:
    print("\n" + "=" * 78)
    print("Q5 ENTRY EDGE - actual entries vs random entries (same corpus)")
    print("=" * 78)
    horizons = ["fwd_ret_1h", "fwd_ret_3h", "fwd_ret_6h", "fwd_mfe_3h"]
    # random baseline: 4000 samples per pair inside the dev corpus
    samples = []
    for pair, frame in frames.items():
        span = frame.loc["2020-01-01":"2025-06-30"]
        idx = RNG.choice(len(span) - 100, size=4000, replace=False)
        samples.append(span.iloc[idx][horizons])
    rand = pd.concat(samples, ignore_index=True)

    print(f"  {'horizon':12} {'actual mean':>12} {'random mean':>12} "
          f"{'edge (bps)':>11} {'95% CI (bps)':>18}")
    for h in horizons:
        act = df[h].dropna().to_numpy()
        rnd = rand[h].dropna().to_numpy()
        edge = act.mean() - rnd.mean()
        boots = [RNG.choice(act, len(act)).mean() - RNG.choice(rnd, len(act)).mean()
                 for _ in range(1000)]
        lo, hi = np.percentile(boots, [2.5, 97.5])
        print(f"  {h:12} {act.mean():>12.5f} {rnd.mean():>12.5f} "
              f"{edge*1e4:>+10.1f} [{lo*1e4:+7.1f}, {hi*1e4:+7.1f}]")
    gross = df["profit_ratio"].mean() + 0.002
    print(f"\n  trade-level: net expectancy {df['profit_ratio'].mean():+.4f}; "
          f"fees +0.0020 round trip => GROSS expectancy {gross:+.4f}")


def main():
    df, frames = build_dataset()
    print(f"dataset: {len(df)} trades, {df['win'].mean():.1%} winners; "
          f"scipy={'yes' if HAVE_SCIPY else 'no'}\n")

    print("=" * 78)
    print("FEATURE IMPORTANCE (AUC 0.5 = no signal; |AUC-0.5|>=0.05 noteworthy;")
    print(" with ~25 features tested, require KS p < 0.002 for significance)")
    print("=" * 78)
    rep = feature_report(df)
    print(rep.to_string(index=False))

    print("\n" + "=" * 78)
    print("TOP-FEATURE QUINTILE DETAIL")
    print("=" * 78)
    for f in rep.head(4)["feature"]:
        quintile_detail(df, f)

    # fingerprints: winners vs losers on the strongest features
    print("\n" + "=" * 78)
    print("WINNER vs LOSER MEDIANS (top 10 features)")
    print("=" * 78)
    for f in rep.head(10)["feature"]:
        w = df.loc[df["win"], f].median()
        l = df.loc[~df["win"], f].median()
        print(f"  {f:18} winners={w:>10.4f}  losers={l:>10.4f}")

    entry_edge(df, frames)

    out = Path("user_data/backtest_results/reports/winning_traits_data.csv")
    df.to_csv(out, index=False)
    print(f"\nfeature dataset: {out}")


if __name__ == "__main__":
    main()
