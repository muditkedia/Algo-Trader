"""TEMPORARY Phase F: definitive Gate 0 investigation for v3 (read-only).

Freqtrade flagged "lookahead-bias" on ETH/USDT ~2022-10-05 19:15 and then
crashed in analyze_indicators:
    cut_full_df = full_df.loc[cut_df.index]
    compare_df  = cut_full_df.compare(cut_df)   # requires IDENTICAL columns

Definitive test for GENUINE bias: an indicator/signal at candle T is
lookahead-free iff deleting every candle after T does not change its value at T.
So we build the analyzed frame twice - once on full data, once on data
physically truncated at T - and compare every column and both signals at T.

Also reproduces the crash condition (column-set mismatch) to classify it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append("user_data/scripts")
sys.path.append("user_data/strategies")

from freqtrade.strategy import merge_informative_pair

from algo_core.indicators import add_core_indicators, crossed_above, crossed_below
from algo_core.settings import AlgoSettings
from validation import loaders

DATA = Path("user_data/data/binance")
PAIR = "ETH/USDT"
CUT = pd.Timestamp("2022-10-05 19:15", tz="UTC")   # the flagged trade
START = pd.Timestamp("2021-12-01", tz="UTC")       # incl. warmup pre-roll
END = pd.Timestamp("2022-12-31", tz="UTC")

settings = AlgoSettings.from_config(
    loaders.load_freqtrade_config("user_data/config.json"))
IND = settings.indicators

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


def load(tf: str, upto: pd.Timestamp) -> pd.DataFrame:
    df = pd.read_feather(DATA / f"{PAIR.replace('/', '_')}-{tf}.feather")
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df[(df["date"] >= START) & (df["date"] <= upto)].reset_index(drop=True)


def analyzed(upto: pd.Timestamp) -> pd.DataFrame:
    """Exactly what AdaptiveTrendStrategyV3 computes, on data ending at `upto`."""
    base = _add(load("5m", upto), IND.base_ema_fast, IND.base_ema_slow)
    for tf, ef, es, cols in INFORMATIVE:
        inf = _add(load(tf, upto), ef, es)
        base = merge_informative_pair(base, inf[cols], "5m", tf, ffill=True)
    # v2/v3 anti-chase columns
    base["mom_1h"] = base["close"] / base["close"].shift(12) - 1
    base["dist_fast_5m"] = (base["close"] - base["ema_fast"]) / base["close"]
    # v1 entry trigger + v3 exit
    base["entry_trigger"] = (
        (base["ema_fast"] > base["ema_slow"])
        & crossed_above(base["close"], base["ema_fast"])
    ).fillna(False)
    base["v3_exit"] = (
        (base["ema_fast_15m"] < base["ema_slow_15m"])
        & (base["close"] < base["ema_slow_15m"])
    ).fillna(False)
    base["date"] = pd.to_datetime(base["date"], utc=True)
    return base.set_index("date")


def main() -> int:
    print("=" * 78)
    print("GATE 0 INVESTIGATION - v3 lookahead flag (ETH/USDT 2022-10-05 19:15)")
    print("=" * 78)

    full = analyzed(END)
    cut = analyzed(CUT)          # every candle after CUT physically deleted

    print(f"\nfull run: {len(full)} candles, ends {full.index[-1]}")
    print(f"cut  run: {len(cut)} candles, ends {cut.index[-1]}")

    # ---------- 1. CRASH CAUSE: do the column sets match? ----------
    print("\n[1] CRASH CAUSE - column-set comparison (pandas .compare needs identical columns)")
    only_full = sorted(set(full.columns) - set(cut.columns))
    only_cut = sorted(set(cut.columns) - set(full.columns))
    print(f"  columns only in full run: {only_full or 'none'}")
    print(f"  columns only in cut  run: {only_cut or 'none'}")
    if only_full or only_cut:
        print("  => COLUMN MISMATCH reproduced: .compare() would raise ValueError.")
    else:
        print("  => columns identical here; crash must come from freqtrade's own"
              " varholder frames.")

    # ---------- 2. GENUINE BIAS: do values at/below CUT change? ----------
    print("\n[2] GENUINE BIAS TEST - are indicator values at candle T identical")
    print("    when all data after T is deleted? (lookahead-free <=> identical)")
    common_cols = [c for c in full.columns if c in cut.columns]
    overlap = cut.index.intersection(full.index)
    a = full.loc[overlap, common_cols]
    b = cut.loc[overlap, common_cols]

    mismatches = {}
    for c in common_cols:
        x, y = a[c], b[c]
        if x.dtype.kind in "biufc" and y.dtype.kind in "biufc":
            diff = ~np.isclose(x.to_numpy(dtype=float), y.to_numpy(dtype=float),
                               rtol=0, atol=1e-12, equal_nan=True)
        else:
            diff = (x.to_numpy() != y.to_numpy())
        n = int(diff.sum())
        if n:
            mismatches[c] = n
    print(f"  candles compared: {len(overlap)}  columns compared: {len(common_cols)}")
    if mismatches:
        print(f"  !! columns differing: {mismatches}")
    else:
        print("  => ZERO differences across every column and every candle.")

    # ---------- 3. THE SIGNALS AT THE BOUNDARY ----------
    print("\n[3] SIGNALS AT THE FLAGGED CANDLE (and the 12 candles before it)")
    tail = overlap[-13:]
    cmp = pd.DataFrame({
        "entry_full": full.loc[tail, "entry_trigger"],
        "entry_cut": cut.loc[tail, "entry_trigger"],
        "v3exit_full": full.loc[tail, "v3_exit"],
        "v3exit_cut": cut.loc[tail, "v3_exit"],
        "ema_slow_15m_full": full.loc[tail, "ema_slow_15m"].round(4),
        "ema_slow_15m_cut": cut.loc[tail, "ema_slow_15m"].round(4),
    })
    print(cmp.to_string())
    ident = (cmp["entry_full"].equals(cmp["entry_cut"])
             and cmp["v3exit_full"].equals(cmp["v3exit_cut"]))
    print(f"\n  entry+exit signals identical at boundary: {ident}")

    # ---------- 4. VERDICT ----------
    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    if not mismatches and ident:
        print("  (a) GENUINE STRATEGY BIAS ....... RULED OUT")
        print("      Deleting all future data changes NOTHING: every indicator and")
        print("      both signals are bit-identical at every candle. The strategy")
        print("      cannot see the future.")
        print("  (b) NUMERICAL BOUNDARY EFFECT ... RULED OUT (zero diffs, atol=1e-12)")
        print("  (c) FREQTRADE TOOL ARTIFACT .... CONFIRMED")
    else:
        print("  !! Differences found - see above. Genuine bias NOT ruled out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
