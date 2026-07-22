"""Phase 12 - the hypothesis-composition framework."""

import numpy as np
import pandas as pd
import pytest

from algo.core.costs import NseEquityCostModel
from algo.evidence.database import EvidenceDB, MEMORY
from algo.research import components
from algo.research.engine import ResearchEngine
from algo.research.hypothesis import (
    Hypothesis, compile_all, compile_hypothesis,
)


def _frame(closes, dates=None):
    closes = np.asarray(closes, float)
    n = len(closes)
    dates = dates if dates is not None else pd.bdate_range(
        "2015-01-01", periods=n, tz="UTC")
    return pd.DataFrame({
        "date": dates, "open": closes, "high": closes * 1.01,
        "low": closes * 0.99, "close": closes, "volume": np.full(n, 1e6)})


# --------------------------------------------------------- validation

def test_requires_exactly_one_of_metric_or_entry():
    with pytest.raises(ValueError, match="exactly one"):
        Hypothesis(name="bad", family="x", hypothesis="h",
                   horizon_bars=(5,), max_hold_bars=5, min_history=10)
    with pytest.raises(ValueError, match="exactly one"):
        Hypothesis(name="bad2", family="x", hypothesis="h",
                   horizon_bars=(5,), max_hold_bars=5, min_history=10,
                   metric=components.momentum(), entry=components.new_high())


def test_metric_requires_cross_sectional():
    with pytest.raises(ValueError, match="cross_sectional"):
        Hypothesis(name="bad3", family="x", hypothesis="h",
                   horizon_bars=(5,), max_hold_bars=5, min_history=10,
                   metric=components.momentum())


# ---------------------------------------- cross-sectional compilation

def test_compiled_momentum_is_deterministic():
    rng = np.random.default_rng(4)
    frames = {f"S{k}": _frame(100.0 * np.exp(np.cumsum(
        rng.normal(0.0003, 0.012, 320)))) for k in range(20)}

    hyp = compile_hypothesis(Hypothesis(
        name="mom_12_1", family="momentum",
        hypothesis="12-1 cross-sectional momentum, top decile",
        metric=components.momentum(252, 21), cross_sectional=True,
        quantile=0.10, rank_top=True, horizon_bars=(21, 42, 63),
        max_hold_bars=63, min_history=280))
    prepared = {s: hyp.prepare(df) for s, df in frames.items()}
    signals = {s: hyp.entry_signal(frame)
               for s, frame in hyp.prepare_cross_section(prepared).items()}
    assert all(len(signal) == len(frames[s]) for s, signal in signals.items())
    assert any(signal.any() for signal in signals.values())


# --------------------------------------------------- per-symbol compilation

def test_compiled_breakout_fires_once_on_channel_break():
    n = 90
    closes = np.full(n, 100.0)
    closes[70:] = 108.0                         # clears the 55-day high
    frame = _frame(closes)

    hyp = compile_hypothesis(Hypothesis(
        name="brk_55", family="breakout",
        hypothesis="55-day channel breakout",
        entry=components.new_high(55), horizon_bars=(10, 20),
        max_hold_bars=20, min_history=60))
    signal = hyp.entry_signal(hyp.prepare(frame))
    assert int(signal.sum()) == 1                # fired once on the break


# ---------------------------------------------- filters + measurability

def test_filter_narrows_signals():
    n = 90
    closes = np.full(n, 10.0)                   # below the min_price floor
    closes[70:] = 12.0
    frame = _frame(closes)
    unfiltered = compile_hypothesis(Hypothesis(
        name="b1", family="breakout", hypothesis="h",
        entry=components.new_high(55), horizon_bars=(10,), max_hold_bars=10,
        min_history=60))
    filtered = compile_hypothesis(Hypothesis(
        name="b2", family="breakout", hypothesis="h",
        entry=components.new_high(55), filters=(components.min_price(20.0),),
        horizon_bars=(10,), max_hold_bars=10, min_history=60))
    assert int(unfiltered.entry_signal(unfiltered.prepare(frame)).sum()) == 1
    assert int(filtered.entry_signal(filtered.prepare(frame)).sum()) == 0


def test_compiled_hypothesis_is_measurable(store):
    """A compiled hypothesis runs through the frozen pipeline and gets a verdict."""
    rng = np.random.default_rng(7)
    symbols = [f"S{k}" for k in range(20)]
    for k, sym in enumerate(symbols):
        store.write(sym, "1d", _frame(100.0 * np.exp(np.cumsum(
            rng.normal(0.0003, 0.012, 340)))))
    db = EvidenceDB(MEMORY)
    eng = ResearchEngine(db, store=store, cost_model=NseEquityCostModel())
    for s in symbols:
        eng.logger_.upsert_instrument(s)
    strat = compile_hypothesis(Hypothesis(
        name="mom_test", family="momentum", hypothesis="momentum",
        metric=components.momentum(126, 21), cross_sectional=True,
        horizon_bars=(21, 42), max_hold_bars=42, min_history=160))
    verdict = eng.measure_all([strat], symbols)[0]
    assert verdict.strategy == "mom_test"
    assert verdict.verdict in ("PASS", "BORDERLINE", "FAIL", "INCONCLUSIVE")
    db.close()


def test_compile_all_grid():
    """A grid of hypotheses compiles to distinct measurable strategies."""
    specs = [Hypothesis(
        name=f"mom_{f}_{q}", family="momentum", hypothesis="grid",
        metric=components.momentum(f, 21), cross_sectional=True, quantile=q,
        horizon_bars=(21,), max_hold_bars=21, min_history=f + 30)
        for f in (126, 252) for q in (0.1, 0.2)]
    strategies = compile_all(specs)
    assert len(strategies) == 4
    assert len({s.name for s in strategies}) == 4
    assert all(s.cross_sectional for s in strategies)   # flag on the strategy
