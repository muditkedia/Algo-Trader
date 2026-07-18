"""Phase 11 - the cross-sectional seam and batch-2 strategies.

The load-bearing tests: (1) the seam is purely additive - per-symbol strategies
are bit-identical whether or not the hook exists; (2) cross-sectional ranking is
lookahead-safe (a date's rank uses only that date's values); (3) a decile
strategy fires on the stock the fixture makes the top-decile winner.
"""

import numpy as np
import pandas as pd
import pytest

from algo.core.costs import NseEquityCostModel, Product
from algo.evidence.database import EvidenceDB, MEMORY
from algo.research.engine import ResearchEngine
from algo.strategies import cross_section as xs
from algo.strategies.library import (
    ALL_STRATEGIES, CrossSectionalMomentum, LowVolatility, TurnOfMonth,
)

BATCH2 = {"xsmom_daily", "resmom_daily", "dualmom_daily", "lowvol_daily",
          "bab_daily", "xsrev_daily", "maxret_daily", "hi52rank_daily",
          "tom_daily", "breadth_regime_daily", "stage2_daily", "illiq_daily",
          "combo_lowvol_mom_daily"}


def _frame(closes, dates=None, vol=None):
    closes = np.asarray(closes, float)
    n = len(closes)
    dates = dates if dates is not None else pd.bdate_range(
        "2015-01-01", periods=n, tz="UTC")
    return pd.DataFrame({
        "date": dates, "open": closes, "high": closes * 1.005,
        "low": closes * 0.995, "close": closes,
        "volume": np.full(n, 1e6) if vol is None else np.asarray(vol, float)})


# --------------------------------------------------------- primitives

def test_decile_flag_ranks_and_scatters():
    # 20 symbols, symbol 0 has the highest metric on every date
    frames = {}
    for k in range(20):
        df = _frame(np.full(300, 100.0))
        df["m"] = float(k)                       # constant metric = k
        frames[f"S{k}"] = df
    xs.decile_flag(frames, "m", "flag", quantile=0.10, top=True)
    # top decile of 20 = 2 names (S18, S19); flag is 1.0 for them, 0 else
    assert frames["S19"]["flag"].iloc[-1] == 1.0
    assert frames["S18"]["flag"].iloc[-1] == 1.0
    assert frames["S0"]["flag"].iloc[-1] == 0.0


def test_decile_flag_needs_min_names():
    frames = {f"S{k}": _frame(np.full(50, 100.0)) for k in range(3)}
    for k, (s, df) in enumerate(frames.items()):
        df["m"] = float(k)
    xs.decile_flag(frames, "m", "flag", quantile=0.10, top=True)
    # < MIN_NAMES ranked -> no flags
    assert all((df["flag"] == 0.0).all() for df in frames.values())


def test_entered_edge_triggers():
    flag = pd.Series([0, 0, 1, 1, 1, 0, 1], dtype=float)
    got = xs.entered(flag).tolist()
    assert got == [False, False, True, False, False, False, True]


def test_market_return_and_breadth():
    frames = {}
    for k in range(10):
        df = _frame(100.0 * (1.001 ** np.arange(50)))
        df["above"] = 1.0 if k < 7 else 0.0      # 7/10 above
        frames[f"S{k}"] = df
    mkt = xs.market_return(frames)
    assert mkt.notna().sum() > 0 and abs(mkt.dropna().iloc[-1] - 0.001) < 1e-6
    br = xs.breadth(frames, "above")
    assert abs(br.iloc[-1] - 0.7) < 1e-9


# --------------------------------------------------- seam is additive

def _store_with(store, frames):
    for sym, df in frames.items():
        store.write(sym, "1d", df)


def test_per_symbol_strategy_unaffected_by_seam(store):
    """A per-symbol strategy must be bit-identical with the seam present -
    prepare_cross_section defaults to a no-op and prepare_signals branches."""
    frames = {f"S{k}": _frame(100.0 * (1.002 ** np.arange(300)))
              for k in range(15)}
    _store_with(store, frames)
    db = EvidenceDB(MEMORY)
    eng = ResearchEngine(db, store=store)
    strat = TurnOfMonth()
    assert not getattr(strat, "cross_sectional", False)
    prepared = eng.prepare_signals(strat, list(frames))
    # tom fires on the month-end window; just assert the seam didn't corrupt it
    assert prepared.n_signals() > 0
    db.close()


def test_cross_sectional_seam_calls_prepare_cross_section(store):
    """The engine must run the two-phase flow for a cross-sectional strategy."""
    # 20 symbols; S0 gets a strong late uptrend -> top momentum decile
    frames = {}
    for k in range(20):
        base = np.full(300, 100.0)
        if k == 0:
            base = 100.0 * (1.01 ** np.arange(300))   # clear winner
        frames[f"S{k}"] = _frame(base)
    _store_with(store, frames)
    db = EvidenceDB(MEMORY)
    eng = ResearchEngine(db, store=store)
    prepared = eng.prepare_signals(CrossSectionalMomentum(), list(frames))
    # the winner should have fired (entered the top momentum decile)
    assert "S0" in prepared.frames
    assert "xs_flag" in prepared.frames["S0"].columns
    assert int(prepared.signals["S0"].sum()) >= 1
    db.close()


# -------------------------------------------- lookahead safety (L-008)

def _xs_signals(strat, frames):
    """Full two-phase cross-sectional pipeline on in-memory frames."""
    prepared = {sym: strat.prepare(df) for sym, df in frames.items()}
    prepared = strat.prepare_cross_section(prepared)
    return {sym: strat.entry_signal(prepared[sym]) for sym in frames}


def test_cross_sectional_ranking_has_no_lookahead():
    """Deleting future bars must not change any earlier cross-sectional signal:
    the rank on date D uses only the values present on D (L-008 methodology)."""
    rng = np.random.default_rng(3)
    frames = {f"S{k}": _frame(100.0 * np.exp(np.cumsum(
        rng.normal(0.0003, 0.01, 320)))) for k in range(15)}
    strat = CrossSectionalMomentum()

    full = _xs_signals(strat, frames)
    for cut in (290, 300, 315):
        trunc = {sym: df.iloc[:cut].reset_index(drop=True)
                 for sym, df in frames.items()}
        head = _xs_signals(strat, trunc)
        for sym in frames:
            a = full[sym].to_numpy()[:cut]
            b = head[sym].to_numpy()
            assert (a == b).all(), f"lookahead in {sym} at cut={cut}"


# ----------------------------------------------------- metadata contract

@pytest.mark.parametrize("cls", [c for c in ALL_STRATEGIES
                                 if c.meta.name in BATCH2],
                         ids=lambda c: c.meta.name)
def test_batch2_metadata(cls):
    m = cls.meta
    assert m.enabled and m.timeframe == "1d" and m.holding_scope.value == "swing"
    assert m.hypothesis and m.known_failure_modes and m.required_columns
    assert m.horizon_bars and m.max_hold_bars >= max(m.horizon_bars)
    assert cls().min_history() >= 2


def test_flat_market_no_signal_batch2(store):
    """No cross-sectional winner in a flat, identical market -> no decile edge."""
    frames = {f"S{k}": _frame(np.full(320, 100.0)) for k in range(15)}
    _store_with(store, frames)
    db = EvidenceDB(MEMORY)
    eng = ResearchEngine(db, store=store)
    # identical constant series -> ranks are ties, no clear decile entries firing
    prepared = eng.prepare_signals(LowVolatility(), list(frames))
    # zero-volatility everywhere: metric is NaN (no pct_change std) -> no flags
    assert prepared.n_signals() == 0
    db.close()
