"""TEMPORARY Phase G: generic entry-edge lab (read-only, no strategies built).

Generalizes measure_entry_edge.py so that a candidate strategy is defined by
ONLY its entry condition - a boolean function of one native-timeframe OHLCV
frame. Everything else (forward-return machinery, MFE/MAE, distribution
statistics, day-clustered bootstrap CIs, fee hurdle, random baseline) is
IMPORTED from measure_entry_edge.py unchanged, so every number here is
produced by exactly the L-009 methodology.

Candidates (entry rules = the research report's default parameters, no
optimization; timeframes adapted to the frozen 30-120 min holding band and
documented per candidate):

  mean_reversion   RSI14 < 30 AND close < SMA20 - 2*ATR14
  pullback         EMA20 > EMA50 AND close <= EMA20 - 2*ATR14 AND RSI14 < 40
  vol_breakout     close > upper BB(20,2) after 3 consecutive prior bars with
                   band width (upper-lower)/mid < 5%
  breakout         close > highest high of the previous 20 candles

Each rule is evaluated on 15m and/or 1h (the report's own intraday
timeframes; its 1D variants imply multi-day holds, out of protocol band).

Lookahead safety:
  * all indicators are causal (talib, prior-candle rolling windows);
  * the 20-candle breakout level uses high.shift(1);
  * native signals are edge-triggered (first candle of a True run only);
  * signals reach the 5m base through freqtrade's merge_informative_pair
    (ffill), the same availability-shift used by the live strategies - a
    15m/1h signal is only visible on the 5m candle that closes when the
    native candle closes.

The AdaptiveTrend v1 entry (profile gate + DecisionEngine GO) is re-run
through the identical pipeline as the benchmark row.

Round-trip fee = 0.2% (0.1%/side, Binance spot). Corpus, pairs, horizons,
thresholds, RNG seed: inherited from measure_entry_edge.py.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

sys.path.append("user_data/scripts")
sys.path.append("user_data/strategies")

import talib.abstract as ta
from freqtrade.strategy import merge_informative_pair

from measure_entry_edge import (
    DATA, FEE, HORIZONS, PAIRS, START, END, THRESHOLDS, RNG,
    build, dist, day_bootstrap, entries as adaptivetrend_entries,
)

OUT_CSV = "user_data/backtest_results/reports/entry_edge_lab_data.csv"


# --------------------------------------------------------------------------
# candidate entry conditions - ONLY the entry rule, nothing else
# --------------------------------------------------------------------------

def cond_mean_reversion(df: pd.DataFrame) -> pd.Series:
    rsi = ta.RSI(df, timeperiod=14)
    sma = ta.SMA(df, timeperiod=20)
    atr = ta.ATR(df, timeperiod=14)
    return (rsi < 30) & (df["close"] < sma - 2.0 * atr)


def cond_pullback(df: pd.DataFrame) -> pd.Series:
    e20 = ta.EMA(df, timeperiod=20)
    e50 = ta.EMA(df, timeperiod=50)
    rsi = ta.RSI(df, timeperiod=14)
    atr = ta.ATR(df, timeperiod=14)
    return (e20 > e50) & (df["close"] <= e20 - 2.0 * atr) & (rsi < 40)


def _bb_width(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    bb = ta.BBANDS(df, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
    upper, mid, lower = bb["upperband"], bb["middleband"], bb["lowerband"]
    return upper, (upper - lower) / mid


def cond_vol_breakout(df: pd.DataFrame) -> pd.Series:
    upper, width = _bb_width(df)
    squeezed_3 = (width < 0.05).shift(1).rolling(3).min() > 0.5
    return (df["close"] > upper) & squeezed_3


def cond_breakout(df: pd.DataFrame) -> pd.Series:
    prior_high = df["high"].shift(1).rolling(20).max()
    return df["close"] > prior_high


@dataclass(frozen=True)
class Candidate:
    name: str
    timeframe: str            # native timeframe the condition is computed on
    condition: Callable[[pd.DataFrame], pd.Series]
    rule: str


CANDIDATES = [
    Candidate("meanrev_15m", "15m", cond_mean_reversion,
              "RSI14<30 & close<SMA20-2*ATR14 on 15m"),
    Candidate("meanrev_1h", "1h", cond_mean_reversion,
              "RSI14<30 & close<SMA20-2*ATR14 on 1h"),
    Candidate("pullback_15m", "15m", cond_pullback,
              "EMA20>EMA50 & close<=EMA20-2*ATR14 & RSI14<40 on 15m"),
    Candidate("pullback_1h", "1h", cond_pullback,
              "EMA20>EMA50 & close<=EMA20-2*ATR14 & RSI14<40 on 1h"),
    Candidate("volbreak_15m", "15m", cond_vol_breakout,
              "close>BBupper(20,2) after 3 bars width<5% on 15m"),
    Candidate("volbreak_1h", "1h", cond_vol_breakout,
              "close>BBupper(20,2) after 3 bars width<5% on 1h"),
    Candidate("breakout_1h", "1h", cond_breakout,
              "close>prior 20-candle high on 1h"),
]


# --------------------------------------------------------------------------
# signal -> 5m-base entries (same availability semantics as the strategies)
# --------------------------------------------------------------------------

def load_native(pair: str, tf: str) -> pd.DataFrame:
    d = pd.read_feather(DATA / f"{pair.replace('/', '_')}-{tf}.feather")
    d["date"] = pd.to_datetime(d["date"], utc=True)
    return d


def candidate_entries(base: pd.DataFrame, pair: str, cand: Candidate
                      ) -> pd.DataFrame:
    """Rows of ``base`` (5m, indexed by date) where the candidate fires."""
    native = load_native(pair, cand.timeframe)
    cond = cand.condition(native).fillna(False).astype(bool)
    fired = cond & ~cond.shift(1, fill_value=False)     # edge-trigger

    sig = native[["date"]].copy()
    sig["sig"] = fired.astype(int)
    bd = base.reset_index()[["date"]]
    merged = merge_informative_pair(bd, sig, "5m", cand.timeframe, ffill=True)
    merged = merged.set_index("date")

    col = merged[f"sig_{cand.timeframe}"].fillna(0)
    first_5m = (col > 0.5) & ~(col.shift(1).fillna(0) > 0.5)
    ts = merged.index[first_5m]
    out = base.loc[base.index.intersection(ts)].copy()
    out["pair"] = pair
    return out


# --------------------------------------------------------------------------
# reporting - identical statistics blocks to measure_entry_edge.main()
# --------------------------------------------------------------------------

def report(name: str, rule: str, ent: pd.DataFrame, rnd: pd.DataFrame) -> dict:
    print("\n" + "=" * 92)
    print(f"{name}  -  {len(ent):,} entries   [{rule}]")
    per_pair = ent["pair"].value_counts().to_dict()
    print(f"  per pair: {per_pair}")
    print("=" * 92)
    if len(ent) < 30:
        print("  TOO FEW SIGNALS for meaningful statistics - reported raw only.")

    summary: dict = {"candidate": name, "n": len(ent)}

    mfe_m = ent["mfe"].mean()
    mae_m = ent["mae"].mean()
    ratio = abs(mfe_m / mae_m) if mae_m else np.nan
    rnd_ratio = abs(rnd["mfe"].mean() / rnd["mae"].mean())
    print("\n[1] MFE / MAE over the 180-min window (before fees)")
    print(f"  MFE mean={mfe_m:+.4f} median={ent['mfe'].median():+.4f}   "
          f"MAE mean={mae_m:+.4f} median={ent['mae'].median():+.4f}")
    print(f"  MFE/|MAE| ratio: entry={ratio:.2f}   random={rnd_ratio:.2f}")
    summary["mfe_mae"] = ratio

    print("\n[2] FORWARD RETURN DISTRIBUTION  (fees = 0.20% round trip)")
    hdr = (f"  {'horizon':8}{'fees':>7}{'mean':>10}{'median':>10}{'p5':>9}"
           f"{'p25':>9}{'p75':>9}{'p95':>9}"
           + "".join(f"{'P>' + f'{t:.1%}':>9}" for t in THRESHOLDS))
    print(hdr)
    for hname in HORIZONS:
        for fee, tag in ((0.0, "gross"), (FEE, "net")):
            d = dist(ent[f"ret_{hname}"].to_numpy(float), hname, fee)
            print(f"  {hname:8}{tag:>7}{d['mean']:>+10.5f}{d['median']:>+10.5f}"
                  f"{d['p5']:>+9.4f}{d['p25']:>+9.4f}{d['p75']:>+9.4f}"
                  f"{d['p95']:>+9.4f}"
                  + "".join(f"{d[f'P>{t:.1%}']:>9.1%}" for t in THRESHOLDS))
            if tag == "gross":
                summary[f"gross_{hname}"] = d["mean"]
                summary[f"median_{hname}"] = d["median"]
                summary[f"p02_{hname}"] = d["P>0.2%"]
            else:
                summary[f"net_{hname}"] = d["mean"]

    print("\n[3] SIGNIFICANCE - mean forward return, day-clustered bootstrap 95% CI")
    print(f"  {'horizon':8}{'entry mean':>12}{'95% CI':>26}{'random mean':>13}"
          f"{'edge vs random':>16}{'beats fees?':>13}")
    for hname in HORIZONS:
        col = f"ret_{hname}"
        m = ent[col].mean()
        lo, hi = day_bootstrap(ent, col)
        rm = rnd[col].mean()
        beats = "YES" if lo > FEE else "NO"
        print(f"  {hname:8}{m:>+12.5f}  [{lo:+.5f}, {hi:+.5f}]{rm:>+13.5f}"
              f"{(m - rm) * 1e4:>+13.1f}bps{beats:>13}")
        summary[f"ci_lo_{hname}"] = lo
        summary[f"ci_hi_{hname}"] = hi
    return summary


def main() -> int:
    print("building 5m frames (indicators + forward returns + MFE/MAE)...")
    frames = {p: build(p) for p in PAIRS}

    # shared random baseline, as in measure_entry_edge.main()
    rnd = []
    for p in PAIRS:
        f = frames[p].dropna(subset=["ret_180m"])
        rnd.append(f.iloc[RNG.choice(len(f), size=min(20000, len(f)),
                                     replace=False)])
    rnd = pd.concat(rnd)

    # diagnostic: how selective is the report's literal 5%-width squeeze?
    print("\nBB(20,2) width<5% squeeze selectivity (fraction of candles):")
    for p in PAIRS:
        for tf in ("15m", "1h"):
            _, width = _bb_width(load_native(p, tf))
            w = width.dropna()
            print(f"  {p} {tf}: {(w < 0.05).mean():.1%}  "
                  f"(median width {w.median():.3%})")

    rows = []
    all_entries = []

    for cand in CANDIDATES:
        ent = pd.concat([candidate_entries(frames[p], p, cand)
                         for p in PAIRS]).sort_index()
        rows.append(report(cand.name, cand.rule, ent, rnd))
        ent["candidate"] = cand.name
        all_entries.append(ent)

    print("\nre-running AdaptiveTrend v1 baseline through the same pipeline...")
    base_ent = pd.concat([adaptivetrend_entries(frames[p], p)
                          for p in PAIRS]).sort_index()
    rows.append(report("adaptivetrend_v1", "profile gate + DecisionEngine GO",
                       base_ent, rnd))
    base_ent["candidate"] = "adaptivetrend_v1"
    all_entries.append(base_ent)

    # ----------------------------------------------------------------------
    print("\n" + "=" * 92)
    print("COMPARISON  (gross mean fwd return in bps; CI = day-clustered 95%; "
          f"fee hurdle {FEE * 1e4:.0f} bps round trip)")
    print("=" * 92)
    hdr = (f"  {'candidate':18}{'n':>7}{'MFE/MAE':>9}"
           f"{'60m':>8}{'120m':>8}{'180m':>8}"
           f"{'CIlo120':>9}{'net120':>8}{'P>.2%@120':>11}"
           f"{'CI>fee':>8}{'>=2xfee':>9}")
    print(hdr)
    for r in rows:
        ci_beats = any(r[f"ci_lo_{h}"] > FEE for h in HORIZONS)
        two_x = any(r[f"gross_{h}"] >= 2 * FEE for h in HORIZONS)
        print(f"  {r['candidate']:18}{r['n']:>7,}{r['mfe_mae']:>9.2f}"
              f"{r['gross_60m'] * 1e4:>+8.1f}{r['gross_120m'] * 1e4:>+8.1f}"
              f"{r['gross_180m'] * 1e4:>+8.1f}"
              f"{r['ci_lo_120m'] * 1e4:>+9.1f}{r['net_120m'] * 1e4:>+8.1f}"
              f"{r['p02_120m']:>11.1%}"
              f"{'YES' if ci_beats else 'no':>8}{'YES' if two_x else 'no':>9}")
    print("\n  CI>fee  : lower 95% bound of the GROSS mean exceeds the 20 bps"
          " cost at ANY horizon")
    print("  >=2xfee : gross mean reaches 40 bps (the D-007 implementation"
          " bar) at ANY horizon")

    keep = ([f"ret_{h}" for h in HORIZONS] + ["mfe", "mae", "pair", "candidate"])
    pd.concat(all_entries)[keep].to_csv(OUT_CSV)
    print(f"\ndata: {OUT_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
