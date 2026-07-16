"""Research engine end-to-end: measure_edge, simulation, evaluation, verdicts,
league table - with a planted-edge PASS control and a noise FAIL control."""

import numpy as np
import pandas as pd
import pytest

from algo.core.enums import Direction, HoldingScope
from algo.evidence.database import EvidenceDB, MEMORY
from algo.research.engine import ResearchEngine
from algo.strategies.base import StrategyMeta, StrategyProfile


class PlantedEdge(StrategyProfile):
    """Fires every 20th bar; the data plants a +2% rise after each signal."""
    meta = StrategyMeta(name="planted", version="1.0", direction=Direction.LONG,
                        holding_scope=HoldingScope.SWING, timeframe="1d",
                        min_bars=10, required_columns=("close",), enabled=True,
                        supported_regimes=("bull",), hypothesis="control")

    def entry_signal(self, df):
        return pd.Series(np.arange(len(df)) % 20 == 10, index=df.index)


class NoiseEntry(StrategyProfile):
    """Fires every 9th bar on a pure random walk - no edge exists."""
    meta = StrategyMeta(name="noise", version="1.0", direction=Direction.LONG,
                        holding_scope=HoldingScope.SWING, timeframe="1d",
                        min_bars=10, required_columns=("close",), enabled=True,
                        supported_regimes=("bull",), hypothesis="control")

    def entry_signal(self, df):
        return pd.Series(np.arange(len(df)) % 9 == 4, index=df.index)


def _write_planted(store, symbols, n_bars=320, seed=5):
    for k, sym in enumerate(symbols):
        rng = np.random.default_rng(seed + k)
        closes = [100.0 * (1 + 0.05 * k)]
        for i in range(1, n_bars):
            if 11 <= (i % 20) <= 14:
                closes.append(closes[-1] * 1.005)
            else:
                closes.append(closes[-1] * (1 + rng.normal(0, 0.0005)))
        closes = np.asarray(closes)
        store.write(sym, "1d", pd.DataFrame({
            "date": pd.bdate_range("2023-01-02", periods=n_bars, tz="UTC"),
            "open": closes, "high": closes * 1.002, "low": closes * 0.998,
            "close": closes, "volume": np.full(n_bars, 1000.0)}))


def _write_noise(store, symbols, n_bars=320, seed=17):
    for k, sym in enumerate(symbols):
        rng = np.random.default_rng(seed + k)
        closes = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n_bars)))
        store.write(sym, "1d", pd.DataFrame({
            "date": pd.bdate_range("2023-01-02", periods=n_bars, tz="UTC"),
            "open": closes, "high": closes * 1.005, "low": closes * 0.995,
            "close": closes, "volume": np.full(n_bars, 1000.0)}))


@pytest.fixture
def engine(store):
    _write_planted(store, ["P0", "P1", "P2"])
    _write_noise(store, ["N0", "N1", "N2"])
    db = EvidenceDB(MEMORY)
    yield ResearchEngine(db, store=store)
    db.close()


def test_measure_edge_detects_planted_and_rejects_noise(engine):
    planted = engine.measure_edge(PlantedEdge(), ["P0", "P1", "P2"])
    assert planted.n_signals >= 30
    assert planted.best().beats_cost

    noise = engine.measure_edge(NoiseEntry(), ["N0", "N1", "N2"])
    assert noise.n_signals >= 30
    assert not any(h.beats_cost for h in noise.horizons)


def test_simulation_and_evaluation_persist(engine):
    sid = engine.logger_.register_strategy("planted", "1.0")
    trades = engine.simulate_strategy(PlantedEdge(), ["P0", "P1", "P2"],
                                      strategy_id=sid)
    assert len(trades) >= 30
    assert {"pair", "profit_ratio", "exit_reason",
            "gross_ratio"} <= set(trades.columns)
    stored = engine.db.connection.execute(
        "SELECT COUNT(*) FROM trades").fetchone()[0]
    assert stored == len(trades)                       # canonical rows persisted

    summary = engine.evaluate_strategy(sid, trades=trades)
    assert summary["trades"] == len(trades)
    assert summary["profit_factor"] > 1.25             # the edge is planted
    assert summary["expectancy"] > 0
    row = engine.db.connection.execute(
        "SELECT profit_factor FROM evaluations WHERE strategy_id=?",
        (sid,)).fetchone()
    assert row["profit_factor"] == pytest.approx(summary["profit_factor"])


def test_cost_sensitivity_degrades_with_multiple(engine):
    sid = engine.logger_.register_strategy("planted", "1.0")
    trades = engine.simulate_strategy(PlantedEdge(), ["P0", "P1", "P2"],
                                      strategy_id=sid, persist=False)
    sens = engine.cost_sensitivity(trades)
    assert sens["2x"]["expectancy"] < sens["1x"]["expectancy"]


def test_measure_all_verdicts_and_league_table(engine):
    verdicts = engine.measure_all(
        [PlantedEdge(), NoiseEntry()],
        ["P0", "P1", "P2", "N0", "N1", "N2"])
    by_name = {v.strategy: v for v in verdicts}

    assert by_name["planted"].verdict == "PASS"
    assert "D-007" in " ".join(by_name["planted"].reasons) \
        or "clears" in " ".join(by_name["planted"].reasons)
    assert by_name["noise"].verdict in ("FAIL", "BORDERLINE")
    assert by_name["noise"].reasons                    # reasons always given

    table = engine.league_table(verdicts)
    assert list(table["strategy"])[0] == "planted"     # PASS ranks first
    assert {"verdict", "expectancy", "win_rate", "profit_factor", "sharpe",
            "sortino", "edge_bps", "cost_bps", "median_hold_min",
            "reasons"} <= set(table.columns)


def test_insufficient_signals_is_inconclusive(engine):
    class Rare(PlantedEdge):
        meta = StrategyMeta(name="rare", version="1.0",
                            direction=Direction.LONG,
                            holding_scope=HoldingScope.SWING, timeframe="1d",
                            min_bars=10, required_columns=("close",),
                            enabled=True)

        def entry_signal(self, df):
            s = pd.Series(False, index=df.index)
            if len(s) > 50:
                s.iloc[50] = True
            return s

    verdicts = engine.measure_all([Rare()], ["P0"])
    assert verdicts[0].verdict == "INCONCLUSIVE"


def test_confidence_calibration_insufficient_without_outcomes(engine):
    sid = engine.logger_.register_strategy("planted", "1.0")
    result = engine.confidence_calibration(sid)
    assert result["status"] == "insufficient evidence"
