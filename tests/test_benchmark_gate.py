"""Phase 10 - the benchmark amendment (D-031).

The load-bearing test is ``test_pure_drift_strategy_now_fails``: a strategy that
enters on arbitrary bars of a rising market clears every ABSOLUTE bar (the L-010
defect) and must now FAIL for lack of selection edge. Everything else guards the
pieces that make that verdict trustworthy: the planted-selection PASS control,
determinism, and the benchmark battery's comparative metrics.
"""

import numpy as np
import pandas as pd
import pytest

from algo.core.costs import NseEquityCostModel, Product
from algo.core.enums import Direction, HoldingScope
from algo.evidence.database import EvidenceDB, MEMORY
from algo.research import benchmarks, edge_lab
from algo.research.engine import ResearchEngine
from algo.strategies.base import StrategyMeta, StrategyProfile


# --------------------------------------------------------------- strategies

class Every7th(StrategyProfile):
    """Enters on arbitrary bars - no market information at all."""
    meta = StrategyMeta(name="every7", version="1.0", direction=Direction.LONG,
                        holding_scope=HoldingScope.SWING, timeframe="1d",
                        min_bars=30, required_columns=("close",), enabled=True,
                        supported_regimes=("bull",), hypothesis="drift control",
                        horizon_bars=(1, 2, 4, 8), max_hold_bars=8)

    def entry_signal(self, df):
        s = pd.Series(np.arange(len(df)) % 7 == 0, index=df.index)
        s.iloc[:30] = False
        return s


 
class SelectsWinners(StrategyProfile):
    """Momentum entry: fires on a >3% up-bar. The fixture plants an EXCESS rise
    only after those bars, so this rule has genuine selection edge that a random
    entry into the same (also-rising) corpus does not."""
    meta = StrategyMeta(name="selects", version="1.0", direction=Direction.LONG,
                        holding_scope=HoldingScope.SWING, timeframe="1d",
                        min_bars=30, required_columns=("close", "day_ret"),
                        enabled=True, supported_regimes=("bull",),
                        hypothesis="selection control",
                        horizon_bars=(1, 2, 4, 8), max_hold_bars=8)

    def prepare(self, df):
        out = df.copy()
        out["day_ret"] = out["close"].pct_change()
        return out

    def entry_signal(self, df):
        if self.missing_columns(df) or len(df) < self.min_history():
            return self.no_signal(df)
        s = df["day_ret"] > 0.03
        s.iloc[:30] = False
        return s.fillna(False)


def _drift_corpus(store, symbols, n=340, drift=0.0025, noise=0.001, seed=1):
    """Uniform upward drift on EVERY bar - entering anywhere earns the drift,
    so no entry rule has selection edge over another."""
    for k, sym in enumerate(symbols):
        rng = np.random.default_rng(seed + k)
        steps = drift + rng.normal(0, noise, n)
        closes = 100.0 * np.exp(np.cumsum(steps))
        store.write(sym, "1d", pd.DataFrame({
            "date": pd.bdate_range("2023-01-02", periods=n, tz="UTC"),
            "open": closes, "high": closes * 1.001, "low": closes * 0.999,
            "close": closes, "volume": np.full(n, 1000.0)}))


def _selection_corpus(store, symbols, n=340, seed=2):
    """A near-flat market with a detectable >3% jump on every 7th bar followed
    by a CONCENTRATED rise on the next two bars, and noise everywhere else. A
    random entry mostly lands on flat bars, so entering on the jump (what
    SelectsWinners does) carries a large selection edge over random - and, being
    a price jump, it survives the store's OHLCV normalization. The edge is both
    absolute (clears cost) and selection (clears random), so this is a clean
    PASS control for the amended gate."""
    for k, sym in enumerate(symbols):
        rng = np.random.default_rng(seed + k)
        closes = [100.0]
        for i in range(1, n):
            if i % 7 == 0:
                step = 0.035                          # detectable jump bar
            elif (i % 7) in (1, 2):
                step = 0.012                          # concentrated post-jump rise
            else:
                step = rng.normal(0, 0.0006)          # flat elsewhere
            closes.append(closes[-1] * np.exp(step))
        closes = np.asarray(closes)
        store.write(sym, "1d", pd.DataFrame({
            "date": pd.bdate_range("2023-01-02", periods=n, tz="UTC"),
            "open": closes, "high": closes * 1.001, "low": closes * 0.999,
            "close": closes, "volume": np.full(n, 1000.0)}))


@pytest.fixture
def drift_engine(store):
    _drift_corpus(store, ["D0", "D1", "D2"])
    db = EvidenceDB(MEMORY)
    eng = ResearchEngine(db, store=store, cost_model=NseEquityCostModel())
    for s in ("D0", "D1", "D2"):
        eng.logger_.upsert_instrument(s)
    yield eng
    db.close()


@pytest.fixture
def selection_engine(store):
    _selection_corpus(store, ["S0", "S1", "S2"])
    db = EvidenceDB(MEMORY)
    eng = ResearchEngine(db, store=store, cost_model=NseEquityCostModel())
    for s in ("S0", "S1", "S2"):
        eng.logger_.upsert_instrument(s)
    yield eng
    db.close()


# ------------------------------------------------ THE regression (L-010)

def test_pure_drift_strategy_now_fails(drift_engine):
    """A rising market makes arbitrary entries look profitable in ABSOLUTE
    terms; the amended gate must reject them for lack of selection edge."""
    edge = drift_engine.measure_edge(Every7th(), ["D0", "D1", "D2"],
                                     product=Product.DELIVERY)
    best_abs = edge.best()
    best_sel = edge.best_selection()

    # absolute edge is real (it is the drift) and would clear the OLD gate
    assert best_abs.gross_mean > 2 * edge.cost_pct
    assert best_abs.ci_low > edge.cost_pct
    # ... but the selection edge is ~0: no better than a random entry
    assert abs(best_sel.selection_mean) < best_abs.gross_mean * 0.5
    assert not best_sel.selection_beats_cost

    verdict = drift_engine.measure_all([Every7th()], ["D0", "D1", "D2"])[0]
    assert verdict.verdict == "FAIL"
    assert any("market drift" in r or "selection" in r for r in verdict.reasons)


def test_selection_strategy_passes(selection_engine):
    """A strategy that genuinely picks the outperforming bars clears the gate,
    proving the amendment credits skill rather than rejecting everything."""
    verdict = selection_engine.measure_all(
        [SelectsWinners()], ["S0", "S1", "S2"])[0]
    edge_dict = verdict.edge
    sel = max((h for h in edge_dict["horizons"]
               if h["selection_bps"] is not None),
              key=lambda h: h["selection_bps"])
    assert sel["selection_bps"] > 0
    assert verdict.verdict in ("PASS", "BORDERLINE")   # genuine selection edge
    if verdict.verdict == "PASS":
        assert sel["selection_ci_bps"][0] > verdict.as_row()["cost_bps"]


def test_drift_and_selection_are_distinguished(drift_engine, selection_engine):
    """The whole point: same absolute-looking edge, opposite verdicts."""
    drift = drift_engine.measure_all([Every7th()], ["D0", "D1", "D2"])[0]
    sel = selection_engine.measure_all(
        [SelectsWinners()], ["S0", "S1", "S2"])[0]
    assert drift.verdict == "FAIL"
    assert sel.verdict in ("PASS", "BORDERLINE")


# ------------------------------------------------------- determinism

def test_selection_ci_is_deterministic(drift_engine):
    a = drift_engine.measure_edge(Every7th(), ["D0", "D1"],
                                  product=Product.DELIVERY)
    b = drift_engine.measure_edge(Every7th(), ["D0", "D1"],
                                  product=Product.DELIVERY)
    for ha, hb in zip(a.horizons, b.horizons):
        assert ha.selection_mean == hb.selection_mean
        assert ha.selection_ci_low == hb.selection_ci_low
        assert ha.selection_ci_high == hb.selection_ci_high


def test_selection_diff_ci_deterministic():
    rng = np.random.default_rng(0)
    sv = pd.Series(rng.normal(0.01, 0.02, 200))
    sd = pd.Series(pd.date_range("2024-01-01", periods=200, freq="1D",
                                 tz="UTC")).dt.normalize()
    rv = pd.Series(rng.normal(0.00, 0.02, 400))
    rd = pd.Series(pd.date_range("2024-01-01", periods=400, freq="12h",
                                 tz="UTC")).dt.normalize()
    assert edge_lab.selection_diff_ci(sv, sd, rv, rd, 4, seed=7) \
        == edge_lab.selection_diff_ci(sv, sd, rv, rd, 4, seed=7)


# ----------------------------------------------------- benchmark battery

def test_benchmark_battery_runs_and_is_deterministic(selection_engine):
    strat = SelectsWinners()
    symbols = ["S0", "S1", "S2"]
    prepared = selection_engine.prepare_signals(strat, symbols)
    trades = selection_engine.simulate_strategy(
        strat, symbols, product=Product.DELIVERY, persist=False,
        prepared=prepared)
    edge = selection_engine.measure_edge(strat, symbols,
                                         product=Product.DELIVERY,
                                         prepared=prepared)
    r1 = benchmarks.run_benchmarks(selection_engine, strat, prepared, trades,
                                   product=Product.DELIVERY, edge=edge).summary()
    r2 = benchmarks.run_benchmarks(selection_engine, strat, prepared, trades,
                                   product=Product.DELIVERY, edge=edge).summary()
    assert r1 == r2                                    # deterministic
    assert r1["excess_vs_random"] is not None
    assert r1["excess_vs_random_ci"][0] is not None    # CI computed
    assert r1["excess_vs_bh"] is not None
    assert r1["random_expectancy"] is not None
    # entering on the planted jumps beats random entry into the same corpus
    assert r1["excess_vs_random"] > 0


def test_random_entries_matched_count_and_seeded(selection_engine):
    strat = SelectsWinners()
    prepared = selection_engine.prepare_signals(strat, ["S0", "S1", "S2"])
    e1 = benchmarks.random_entries(prepared, 50, seed=101, min_index=30,
                                   max_lead=8)
    e2 = benchmarks.random_entries(prepared, 50, seed=101, min_index=30,
                                   max_lead=8)
    e3 = benchmarks.random_entries(prepared, 50, seed=202, min_index=30,
                                   max_lead=8)
    total = sum(len(v) for v in e1.values())
    assert total == 50                                 # matched to the count
    assert {k: v.tolist() for k, v in e1.items()} == \
        {k: v.tolist() for k, v in e2.items()}         # seeded
    assert {k: v.tolist() for k, v in e1.items()} != \
        {k: v.tolist() for k, v in e3.items()}         # seed actually varies


def test_benchmark_verdict_carries_metrics(selection_engine):
    v = selection_engine.measure_all([SelectsWinners()],
                                     ["S0", "S1", "S2"], benchmarks=True)[0]
    assert v.benchmarks is not None
    row = v.as_row()
    assert "excess_vs_random" in row and "excess_vs_bh" in row
    assert row["selection_bps"] is not None
