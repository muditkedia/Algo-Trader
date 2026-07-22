"""Phase 8 research pipeline: discovery, pre-registered horizons, single-pass
signal computation, the one-call research loop, and evidence rendering.

The load-bearing test here is ``test_research_all_matches_measure_all``: the
pipeline got faster and easier to extend, and the verdicts it issues did not
move. Everything else is only useful if that holds.
"""

import sys
import textwrap

import numpy as np
import pandas as pd
import pytest

from algo.core.enums import Direction, HoldingScope
from algo.evidence.database import EvidenceDB, MEMORY
from algo.research import edge_lab, reporting
from algo.research.engine import ResearchEngine, product_for_strategy
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.library import ALL_STRATEGIES, REGISTRY
from algo.strategies.registry import StrategyRegistry


class Every20th(StrategyProfile):
    """Fires every 20th bar; the fixture plants a rise after each signal."""

    meta = StrategyMeta(name="every20", version="1.0", direction=Direction.LONG,
                        holding_scope=HoldingScope.SWING, timeframe="1d",
                        min_bars=10, required_columns=("close",), enabled=True,
                        supported_regimes=("bull",), hypothesis="control")

    def __init__(self, settings=None):
        super().__init__(settings)
        self.prepare_calls = 0
        self.signal_calls = 0

    def prepare(self, dataframe):
        self.prepare_calls += 1
        return dataframe.copy()

    def entry_signal(self, dataframe):
        self.signal_calls += 1
        return pd.Series(np.arange(len(dataframe)) % 20 == 10,
                         index=dataframe.index)


def _write_planted(store, symbols, n_bars=320, seed=5):
    for k, symbol in enumerate(symbols):
        rng = np.random.default_rng(seed + k)
        closes = [100.0 * (1 + 0.05 * k)]
        for i in range(1, n_bars):
            if 11 <= (i % 20) <= 14:
                closes.append(closes[-1] * 1.005)
            else:
                closes.append(closes[-1] * (1 + rng.normal(0, 0.0005)))
        closes = np.asarray(closes)
        store.write(symbol, "1d", pd.DataFrame({
            "date": pd.bdate_range("2023-01-02", periods=n_bars, tz="UTC"),
            "open": closes, "high": closes * 1.002, "low": closes * 0.998,
            "close": closes, "volume": np.full(n_bars, 1000.0)}))


@pytest.fixture
def planted_engine(store):
    _write_planted(store, ["P0", "P1", "P2"])
    db = EvidenceDB(MEMORY)
    engine = ResearchEngine(db, store=store)
    for symbol in ("P0", "P1", "P2"):
        engine.logger_.upsert_instrument(symbol)     # FK target for signals
    yield engine
    db.close()


# ----------------------------------------------------------- A) discovery

def test_library_is_discovered_not_hand_listed():
    """ALL_STRATEGIES is derived from discovery, so a new module is picked up
    with no edit to the package __init__."""
    assert tuple(REGISTRY.get(n) for n in REGISTRY.names()) == ALL_STRATEGIES
    assert {"ema200_daily", "nr7_daily", "orb_15m", "pullback_15m",
            "volexp_1h", "vwap_15m"} <= {c.meta.name for c in ALL_STRATEGIES}


def test_discovered_classes_are_importable_by_name():
    """Discovery must not cost the ergonomics of a normal import."""
    from algo.strategies.library import Ema200PullbackTrend
    assert Ema200PullbackTrend in ALL_STRATEGIES


def test_library_order_is_deterministic():
    """Sweep order must not depend on filesystem or import order (SS23)."""
    names = [cls.meta.name for cls in ALL_STRATEGIES]
    assert names == sorted(names)


def test_strategy_names_are_unique_at_import():
    names = [cls.meta.name for cls in ALL_STRATEGIES]
    assert len(names) == len(set(names))


def test_dropping_a_module_into_a_package_registers_it(tmp_path, monkeypatch):
    """The actual Part-E promise: one new file, zero registration."""
    pkg = tmp_path / "plugin_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "brand_new.py").write_text(textwrap.dedent('''
        import pandas as pd
        from algo.core.enums import Direction, HoldingScope
        from algo.strategies.base import StrategyMeta, StrategyProfile

        class BrandNew(StrategyProfile):
            meta = StrategyMeta(name="brand_new", version="1.0",
                                direction=Direction.LONG,
                                holding_scope=HoldingScope.SWING,
                                timeframe="1d", enabled=True)

            def entry_signal(self, dataframe):
                return self.no_signal(dataframe)
    '''))
    monkeypatch.syspath_prepend(str(tmp_path))
    registry = StrategyRegistry()
    try:
        assert registry.discover("plugin_pkg") == ["brand_new"]
        assert "brand_new" in registry
        assert registry.create("brand_new").name == "brand_new"
    finally:
        for module in [m for m in sys.modules if m.startswith("plugin_pkg")]:
            del sys.modules[module]


def test_discover_accepts_an_imported_package():
    """A package must be able to discover itself from its own __init__."""
    registry = StrategyRegistry()
    found = registry.discover(sys.modules["algo.strategies.library"])
    assert sorted(found) == [c.meta.name for c in ALL_STRATEGIES]


# -------------------------------------------- B) pre-registered horizons

def test_horizon_defaults_reproduce_the_phase7_measurement():
    """The D-026 verdicts must stay reproducible: the defaults ARE the
    horizons the six rejected strategies were measured at. Later batches
    pre-register their own horizons - only the ORIGINAL six are pinned."""
    meta = StrategyMeta(name="d", version="1.0", direction=Direction.LONG,
                        holding_scope=HoldingScope.SWING)
    assert meta.horizon_bars == edge_lab.DEFAULT_HORIZON_BARS == (1, 2, 4, 8)
    assert meta.max_hold_bars == 8
    phase7_six = {"ema200_daily", "nr7_daily", "orb_15m", "pullback_15m",
                  "volexp_1h", "vwap_15m"}
    for cls in ALL_STRATEGIES:
        if cls.meta.name in phase7_six:
            assert cls.meta.horizon_bars == (1, 2, 4, 8)
            assert cls.meta.max_hold_bars == 8


def test_horizon_longer_than_hold_is_rejected():
    with pytest.raises(ValueError, match="max_hold_bars"):
        StrategyMeta(name="bad", version="1.0", direction=Direction.LONG,
                     holding_scope=HoldingScope.SWING,
                     horizon_bars=(1, 60), max_hold_bars=8)


@pytest.mark.parametrize("horizons", [(), (0, 4), (-1,)])
def test_invalid_horizons_are_rejected(horizons):
    with pytest.raises(ValueError, match="horizon_bars"):
        StrategyMeta(name="bad", version="1.0", direction=Direction.LONG,
                     holding_scope=HoldingScope.SWING, horizon_bars=horizons,
                     max_hold_bars=100)


def test_declared_horizon_drives_measurement(planted_engine):
    """A strategy is measured at the horizon its own hypothesis declares."""
    class Slow(Every20th):
        meta = StrategyMeta(name="slow", version="1.0",
                            direction=Direction.LONG,
                            holding_scope=HoldingScope.SWING, timeframe="1d",
                            min_bars=10, required_columns=("close",),
                            enabled=True, horizon_bars=(1, 5, 20),
                            max_hold_bars=20)

    edge = planted_engine.measure_edge(Slow(), ["P0", "P1", "P2"])
    assert [h.bars for h in edge.horizons] == [1, 5, 20]


def test_declared_hold_drives_simulation(planted_engine):
    """max_hold_bars is honoured from meta, so a swing candidate is not
    force-closed at the 8-bar horizon the intraday six were measured at."""
    class Slow(Every20th):
        meta = StrategyMeta(name="slow_sim", version="1.0",
                            direction=Direction.LONG,
                            holding_scope=HoldingScope.SWING, timeframe="1d",
                            min_bars=10, required_columns=("close",),
                            enabled=True, horizon_bars=(1, 20),
                            max_hold_bars=20)

    kwargs = dict(persist=False, product=product_for_strategy(Every20th()))
    short = planted_engine.simulate_strategy(Every20th(), ["P0"], **kwargs)
    long_ = planted_engine.simulate_strategy(Slow(), ["P0"], **kwargs)
    # Each strategy's OWN declared cap binds. Durations are wall-clock and the
    # bars are business days, so 8 bars spans 10 calendar days and 20 spans 28.
    assert set(short["exit_reason"]) == {"horizon_end"}
    assert short["trade_duration"].max() == 14400.0        # the default 8 bars
    assert long_["trade_duration"].max() == 40320.0        # its declared 20


# ------------------------------------------------- C) single-pass throughput

def test_prepare_and_signal_are_computed_once_per_symbol(planted_engine):
    """Recording, edge measurement and simulation each used to recompute the
    indicators: three passes per strategy per symbol. The whole sweep now makes
    exactly one."""
    strategy = Every20th()
    symbols = ["P0", "P1", "P2"]
    planted_engine.research(strategy, symbols)
    assert strategy.prepare_calls == len(symbols)
    assert strategy.signal_calls == len(symbols)


def test_prepare_signals_keeps_only_symbols_that_fire(planted_engine):
    class Never(Every20th):
        meta = StrategyMeta(name="never", version="1.0",
                            direction=Direction.LONG,
                            holding_scope=HoldingScope.SWING, timeframe="1d",
                            min_bars=10, required_columns=("close",),
                            enabled=True)

        def entry_signal(self, dataframe):
            return self.no_signal(dataframe)

    prepared = planted_engine.prepare_signals(Never(), ["P0", "P1"])
    assert not prepared and prepared.n_signals() == 0

    fired = planted_engine.prepare_signals(Every20th(), ["P0", "MISSING"])
    assert set(fired.frames) == {"P0"}        # unknown symbol simply absent
    assert fired and fired.n_signals() > 0


def test_signal_set_can_be_passed_in(planted_engine):
    strategy = Every20th()
    prepared = planted_engine.prepare_signals(strategy, ["P0"])
    calls = strategy.prepare_calls
    planted_engine.measure_edge(strategy, ["P0"], prepared=prepared)
    planted_engine.simulate_strategy(strategy, ["P0"], prepared=prepared,
                                     persist=False)
    assert strategy.prepare_calls == calls        # nothing recomputed


# ------------------------------------------------ D) the research pipeline

def test_research_records_labels_and_judges(planted_engine):
    verdict = planted_engine.research(Every20th(), ["P0", "P1", "P2"])
    assert verdict.verdict == "PASS"              # the edge is planted
    assert verdict.n_recorded > 30
    assert verdict.n_labeled > 0

    conn = planted_engine.db.connection
    assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] \
        == verdict.n_recorded
    assert conn.execute(
        "SELECT COUNT(*) FROM signal_outcomes").fetchone()[0] == verdict.n_labeled
    assert conn.execute(
        "SELECT COUNT(*) FROM evaluations").fetchone()[0] >= 1


def test_research_is_idempotent(planted_engine):
    """Re-running the pipeline must never duplicate evidence."""
    first = planted_engine.research(Every20th(), ["P0", "P1"])
    second = planted_engine.research(Every20th(), ["P0", "P1"])
    assert second.n_recorded == 0                 # all already on record
    assert second.verdict == first.verdict
    total = planted_engine.db.connection.execute(
        "SELECT COUNT(*) FROM signals").fetchone()[0]
    assert total == first.n_recorded


def test_research_all_matches_measure_all(planted_engine, store):
    """THE regression guard: the Phase-8 pipeline judges exactly as before.

    Same strategies, same bars, separate evidence DBs - the verdicts and the
    reasons behind them must be identical.
    """
    symbols = ["P0", "P1", "P2"]
    measured = planted_engine.measure_all([Every20th()], symbols)

    db = EvidenceDB(MEMORY)
    try:
        engine = ResearchEngine(db, store=store)
        for symbol in symbols:
            engine.logger_.upsert_instrument(symbol)      # FK target
        researched = engine.research_all([Every20th()], symbols)
    finally:
        db.close()

    assert [v.verdict for v in researched] == [v.verdict for v in measured]
    assert [v.reasons for v in researched] == [v.reasons for v in measured]
    assert [v.edge for v in researched] == [v.edge for v in measured]


def test_research_all_reports_progress(planted_engine):
    seen = []
    planted_engine.research_all([Every20th()], ["P0"], on_verdict=seen.append)
    assert [v.strategy for v in seen] == ["every20"]


def test_product_follows_declared_holding_scope():
    from algo.core.costs import Product

    class Intra(Every20th):
        meta = StrategyMeta(name="intra", version="1.0",
                            direction=Direction.LONG,
                            holding_scope=HoldingScope.INTRADAY,
                            timeframe="15m", enabled=True)

    assert product_for_strategy(Intra()) == Product.INTRADAY
    assert product_for_strategy(Every20th()) == Product.DELIVERY


# --------------------------------------------------------- E) reporting

def test_reporting_renders_table_and_reasons(planted_engine):
    verdicts = planted_engine.research_all([Every20th()], ["P0", "P1", "P2"])
    table = planted_engine.league_table(verdicts)

    text = reporting.league_table_text(table)
    assert "every20" in text and "PASS" in text

    markdown = reporting.league_table_markdown(
        table, verdicts, data_note="unit test corpus")
    assert "1 candidate(s) measured: 1 PASS" in markdown
    assert "unit test corpus" in markdown
    assert "signals recorded" in markdown
    for reason in verdicts[0].reasons:
        assert reason in markdown


def test_reporting_handles_an_empty_sweep():
    assert "no candidates" in reporting.league_table_text(pd.DataFrame())
    assert "0 candidate(s)" in reporting.league_table_markdown(
        pd.DataFrame(), [], data_note="none")


def test_verdict_detail_lists_every_reason(planted_engine):
    verdicts = planted_engine.research_all([Every20th()], ["P0"])
    detail = reporting.verdict_detail_text(verdicts)
    assert "every20" in detail
    for reason in verdicts[0].reasons:
        assert reason in detail


# ---------------------------------------------------- F) script wiring

def test_select_strategies_filters_and_rejects_unknown():
    sys.path.insert(0, "scripts")
    try:
        from run_measurement import select_strategies
    finally:
        sys.path.pop(0)

    assert len(select_strategies(None)) == len(ALL_STRATEGIES)
    assert [s.name for s in select_strategies("ema200_daily")] == ["ema200_daily"]
    with pytest.raises(SystemExit, match="unknown strategy"):
        select_strategies("does_not_exist")
