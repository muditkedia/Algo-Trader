"""TEMPORARY Phase D root-cause diagnostic (reuses algo_core READ-ONLY; changes nothing).

Answers the four Phase D questions from the existing Phase C export + Phase B
data, with one cheap indicator pass per pair (no re-backtest):

  Q1 Entry quality  - reconstruct entry-time decision state (conviction/advisory
                      score, ADX/RSI/volume/ATR%/structure, RR) and compare
                      winners vs losers. Does any entry metric predict outcome?
  Q2 Exit quality   - MFE/MAE + give-back straight from max_rate/min_rate; how
                      much PnL is destroyed by late exits; per exit reason; peak
                      timing / exit lag from 5m candles.
  Q3 Risk/reward    - distribution + predictive power of the entry RR gate.
  Q4 Regime         - why bull markets lose: entry timing vs exit give-back.

Nothing here mutates state, tunes, or optimizes. It only measures.
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
from algo_core.risk_engine import initial_stop_pct
from algo_core.settings import AlgoSettings
from validation import loaders
from validation import regime as R

DATA = Path("user_data/data/binance")
PAIRS = ["BTC/USDT", "ETH/USDT"]
ZIP = "user_data/backtest_results/backtest-result-2026-07-14_09-19-41.zip"
STAKE = 100.0

settings = AlgoSettings.from_config(
    loaders.load_freqtrade_config("user_data/config.json"))
engine = DecisionEngine(settings.engine, settings.risk)
IND = settings.indicators

INFORMATIVE = [
    ("15m", IND.ema_15m_fast, IND.ema_15m_slow,
     ["date", "close", "ema_fast", "ema_slow", "adx", "rsi", "atr",
      "atr_pct", "swing_low", "structure_up"]),
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


def analyzed(pair: str) -> pd.DataFrame:
    base = _add(pd.read_feather(DATA / f"{pair.replace('/', '_')}-5m.feather"),
                IND.base_ema_fast, IND.base_ema_slow)
    for tf, ef, es, cols in INFORMATIVE:
        inf = _add(pd.read_feather(DATA / f"{pair.replace('/', '_')}-{tf}.feather"),
                   ef, es)
        base = merge_informative_pair(base, inf[cols], "5m", tf, ffill=True)
    base["date"] = pd.to_datetime(base["date"], utc=True)
    return base.set_index("date")


def build() -> pd.DataFrame:
    trades = loaders.load_backtest_export(ZIP)
    labels = {p: R.label_daily(
        pd.read_feather(DATA / f"{p.replace('/', '_')}-1d.feather")) for p in PAIRS}
    trades = R.tag_trades(trades, labels)
    frames = {p: analyzed(p) for p in PAIRS}

    recs = []
    recomputed_go = 0
    for t in trades.itertuples():
        frame = frames[t.pair]
        pos = int(frame.index.searchsorted(t.open_date))
        row = frame.iloc[max(pos - 1, 0)]
        ctx = TradeContext(t.pair, "long", row, STAKE, None,
                           getattr(t, "entry_trend_regime", "trend"))
        dec = engine.evaluate(ctx)
        recomputed_go += int(dec.go)

        close = float(row.get("close"))
        atr15 = row.get("atr_15m")
        swing = row.get("swing_low_15m")
        stop_pct = initial_stop_pct(close, atr15, swing, settings.risk)
        rr = (settings.risk.reward_atr_multiple * atr15 / close / stop_pct
              if stop_pct and atr15 else np.nan)

        mfe = (t.max_rate - t.open_rate) / t.open_rate
        mae = (t.min_rate - t.open_rate) / t.open_rate
        seg = frame.loc[t.open_date:t.close_date]
        lag_min = np.nan
        if len(seg) and "high" in seg:
            peak_time = seg["high"].idxmax()
            lag_min = (t.close_date - peak_time).total_seconds() / 60.0

        recs.append({
            "pair": t.pair, "win": bool(t.profit_abs > 0),
            "profit_ratio": float(t.profit_ratio), "profit_abs": float(t.profit_abs),
            "exit_reason": t.exit_reason, "duration": float(t.trade_duration),
            "regime": getattr(t, "entry_trend_regime", "?"),
            "score": dec.score,
            "adx_1h": row.get("adx_1h"), "adx_15m": row.get("adx_15m"),
            "rsi_15m": row.get("rsi_15m"), "volume_ratio": row.get("volume_ratio"),
            "atr_pct_15m": row.get("atr_pct_15m"),
            "structure_up_15m": row.get("structure_up_15m"),
            "rr": rr, "stop_pct": stop_pct,
            "mfe": mfe, "mae": mae, "give_back": mfe - float(t.profit_ratio),
            "exit_lag_min": lag_min,
        })
    df = pd.DataFrame(recs)
    print(f"reconstruction fidelity: {recomputed_go}/{len(df)} "
          f"({recomputed_go/len(df):.1%}) recompute as GO on the signal candle\n")
    return df


def _cmp(df, col):
    w = df.loc[df["win"], col].dropna()
    l = df.loc[~df["win"], col].dropna()
    # point-biserial: corr of metric with win flag
    sub = df[[col, "win"]].dropna()
    pb = sub[col].corr(sub["win"].astype(float)) if len(sub) > 5 else np.nan
    return (f"{col:16} win: med={w.median():8.3f} mean={w.mean():8.3f} | "
            f"loss: med={l.median():8.3f} mean={l.mean():8.3f} | "
            f"corr_with_win={pb:+.3f}")


def q1_entry(df):
    print("=" * 78)
    print("Q1 ENTRY QUALITY - winners vs losers (corr_with_win ~0 => no discrimination)")
    print("=" * 78)
    for col in ("score", "adx_1h", "adx_15m", "rsi_15m", "volume_ratio",
                "atr_pct_15m", "structure_up_15m", "rr"):
        print("  " + _cmp(df, col))
    n = len(df)
    print(f"\n  win rate={df['win'].mean():.1%}  trades={n}")
    print("  => If every corr_with_win is near 0, the GO engine cannot tell "
          "winners from losers:\n     it admits bad trades, and its filters "
          "carry no predictive signal.")


def q2_exit(df):
    print("\n" + "=" * 78)
    print("Q2 EXIT QUALITY - give-back and PnL destroyed by late exits")
    print("=" * 78)
    df["mfe_usdt"] = df["mfe"] * STAKE
    df["give_back_usdt"] = df["give_back"] * STAKE
    total_gb = df["give_back_usdt"].sum()
    peaked = df[df["mfe"] >= 0.005]
    destroyed = peaked[peaked["profit_abs"] <= 0]
    winners = df[df["win"]]
    print(f"  mean MFE (all)          : {df['mfe'].mean():+.4f}")
    print(f"  mean realized (all)     : {df['profit_ratio'].mean():+.4f}")
    print(f"  mean give-back (all)    : {df['give_back'].mean():+.4f} "
          f"(total {total_gb:+.1f} USDT left on the table)")
    print(f"  winners: mean MFE {winners['mfe'].mean():+.4f} vs realized "
          f"{winners['profit_ratio'].mean():+.4f} "
          f"=> capture {winners['profit_ratio'].mean()/winners['mfe'].mean():.0%}")
    print(f"\n  trades that reached >= +0.5% MFE : {len(peaked)}")
    print(f"    of those, closed <= 0 (destroyed winners): {len(destroyed)} "
          f"({len(destroyed)/max(len(peaked),1):.0%})")
    print(f"    realized on destroyed winners  : {destroyed['profit_abs'].sum():+.1f} USDT")
    print(f"    MFE that was available on them : "
          f"{(destroyed['mfe']*STAKE).sum():+.1f} USDT")
    print("\n  by exit reason (mean MFE / mean realized / give-back / exit lag):")
    g = df.groupby("exit_reason").agg(
        n=("mfe", "size"), mfe=("mfe", "mean"), realized=("profit_ratio", "mean"),
        give_back=("give_back", "mean"), lag_min=("exit_lag_min", "median"),
        net_usdt=("profit_abs", "sum"))
    for reason, r in g.iterrows():
        print(f"    {reason:24} n={int(r['n']):4} mfe={r['mfe']:+.4f} "
              f"realized={r['realized']:+.4f} give_back={r['give_back']:+.4f} "
              f"lag={r['lag_min']:6.0f}min net={r['net_usdt']:+.1f}")


def q3_rr(df):
    print("\n" + "=" * 78)
    print("Q3 RISK/REWARD GATE - distribution and predictive power (accepted trades)")
    print("=" * 78)
    rr = df["rr"].dropna()
    print(f"  entry RR: min={rr.min():.3f} p25={rr.quantile(.25):.3f} "
          f"median={rr.median():.3f} p75={rr.quantile(.75):.3f} max={rr.max():.3f} "
          f"std={rr.std():.3f}")
    print(f"  share at exactly ~1.5 (ATR stop dominates): "
          f"{((rr > 1.49) & (rr < 1.51)).mean():.1%}")
    sub = df[["rr", "profit_ratio", "win"]].dropna()
    print(f"  corr(entry RR, realized profit) = {sub['rr'].corr(sub['profit_ratio']):+.3f}")
    print(f"  corr(entry RR, win)             = "
          f"{sub['rr'].corr(sub['win'].astype(float)):+.3f}")
    print("  expectancy by RR bin:")
    for lo, hi in [(1.3, 1.4), (1.4, 1.5), (1.5, 1.51), (1.51, 3)]:
        b = sub[(sub["rr"] >= lo) & (sub["rr"] < hi)]
        if len(b):
            print(f"    RR [{lo:.2f},{hi:.2f}): n={len(b):4} "
                  f"mean_realized={b['profit_ratio'].mean():+.4f} "
                  f"win={b['win'].mean():.1%}")
    # rejection log: how many risk_reward NO-GOs and their rr
    rej = loaders.load_rejections("user_data/logs/trade_rejections.jsonl")
    if not rej.empty:
        rr_rej = 0
        for reasons in rej.get("rejection_reasons", []):
            rr_rej += sum(1 for x in (reasons or []) if x.startswith("risk_reward"))
        print(f"\n  risk_reward NO-GO count in audit log: {rr_rej}")
    print("  => If RR is a near-constant ~1.5 and uncorrelated with outcome, the "
          "gate\n     filters volume without improving expectancy and measures a "
          "fixed ATR\n     assumption, not a real target.")


def q4_regime(df):
    print("\n" + "=" * 78)
    print("Q4 REGIME - why bull markets lose")
    print("=" * 78)
    g = df.groupby("regime").agg(
        n=("win", "size"), win=("win", "mean"), net=("profit_abs", "sum"),
        mfe=("mfe", "mean"), realized=("profit_ratio", "mean"),
        give_back=("give_back", "mean"), lag=("exit_lag_min", "median"))
    for reg, r in g.iterrows():
        pf_hint = ""
        print(f"  {reg:12} n={int(r['n']):4} win={r['win']:.1%} "
              f"net={r['net']:+8.1f} mfe={r['mfe']:+.4f} "
              f"realized={r['realized']:+.4f} give_back={r['give_back']:+.4f} "
              f"lag={r['lag']:5.0f}m")
    bull = df[df["regime"] == "bull"]
    if len(bull):
        bw, bl = bull[bull["win"]], bull[~bull["win"]]
        print(f"\n  BULL: {len(bull)} trades, {bull['win'].mean():.1%} win, "
              f"net {bull['profit_abs'].sum():+.1f} USDT")
        print(f"    bull winners: mfe {bw['mfe'].mean():+.4f} realized "
              f"{bw['profit_ratio'].mean():+.4f} give_back {bw['give_back'].mean():+.4f}")
        print(f"    bull losers : mfe {bl['mfe'].mean():+.4f} realized "
              f"{bl['profit_ratio'].mean():+.4f} mae {bl['mae'].mean():+.4f}")
        print(f"    bull trades that reached >=+0.5% MFE then closed <=0: "
              f"{len(bull[(bull['mfe']>=0.005)&(bull['profit_abs']<=0)])}")
        print("  => High bull MFE with negative realized = exits give back gains "
              "(exit-timing),\n     not misclassification. Low bull MFE = entry "
              "timing (buying local tops).")


def main():
    df = build()
    q1_entry(df)
    q2_exit(df)
    q3_rr(df)
    q4_regime(df)
    out = Path("user_data/backtest_results/reports/root_cause_data.csv")
    df.to_csv(out, index=False)
    print(f"\nper-trade diagnostic data: {out}")


if __name__ == "__main__":
    main()
