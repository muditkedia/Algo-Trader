"""Research Engine skeleton: evidence -> validation battery -> evaluations."""

import pandas as pd
import pytest

from algo.evidence.database import EvidenceDB, MEMORY
from algo.evidence.logger import EvidenceLogger
from algo.evidence.models import TradeRecord
from algo.research.engine import ResearchEngine


def _seed_trades(log: EvidenceLogger, strategy_id: int, n: int = 24) -> None:
    base = pd.Timestamp("2024-01-01 09:20", tz="UTC")
    for i in range(n):
        open_dt = base + pd.Timedelta(days=i)
        close_dt = open_dt + pd.Timedelta(minutes=60)
        pr = 0.006 if i % 3 else -0.004        # a mild positive edge
        log.record_trade(TradeRecord(
            mode="backtest", symbol="RELIANCE",
            open_date=open_dt.isoformat(), close_date=close_dt.isoformat(),
            profit_ratio=pr, profit_abs=pr * 10000.0, stake_amount=10000.0,
            strategy_id=strategy_id))


@pytest.fixture
def engine_with_trades():
    db = EvidenceDB(MEMORY)
    log = EvidenceLogger(db)
    sid = log.register_strategy("dummy", "1.0")
    _seed_trades(log, sid)
    yield ResearchEngine(db), sid
    db.close()


def test_trades_frame_canonical_schema(engine_with_trades):
    engine, sid = engine_with_trades
    frame = engine.trades_frame(sid)
    assert len(frame) == 24
    assert {"pair", "open_date", "close_date", "profit_abs",
            "trade_duration"} <= set(frame.columns)
    # duration derived from dates (60 min)
    assert frame["trade_duration"].iloc[0] == pytest.approx(60.0)
    assert str(frame["open_date"].dtype).startswith("datetime64")


def test_evaluate_strategy_writes_evaluation(engine_with_trades):
    engine, sid = engine_with_trades
    summary = engine.evaluate_strategy(sid, start_capital=100_000.0)
    assert summary["trades"] == 24
    assert "profit_factor" in summary and "expectancy" in summary
    row = engine.db.connection.execute(
        "SELECT scope_type, n_trades FROM evaluations WHERE strategy_id=?",
        (sid,)).fetchone()
    assert row["scope_type"] == "overall" and row["n_trades"] == 24


def test_league_table_ranks_strategies(engine_with_trades):
    engine, sid = engine_with_trades
    engine.evaluate_strategy(sid)
    table = engine.league_table()
    assert not table.empty
    assert "dummy" in table["name"].tolist()


def test_validate_produces_report(engine_with_trades, tmp_path):
    engine, sid = engine_with_trades
    frame = engine.trades_frame(sid)
    out = tmp_path / "report.md"
    results = engine.validate(frame, out)
    assert out.exists()
    assert results["verdict"]["status"] in ("PASS", "FAIL", "INCONCLUSIVE")


def test_no_trades_strategy_records_empty_eval():
    db = EvidenceDB(MEMORY)
    log = EvidenceLogger(db)
    sid = log.register_strategy("empty", "1.0")
    engine = ResearchEngine(db)
    assert engine.evaluate_strategy(sid) == {}
    row = db.connection.execute(
        "SELECT verdict FROM evaluations WHERE strategy_id=?", (sid,)).fetchone()
    assert row["verdict"] == "no_trades"
    db.close()


def test_market_data_methods_require_a_store():
    # Phase 5 implemented measure_edge/label_outcomes; without a
    # MarketDataStore they must refuse loudly, not silently no-op.
    engine = ResearchEngine(EvidenceDB(MEMORY))     # no store wired
    with pytest.raises(RuntimeError):
        engine.measure_edge(None, [])
    with pytest.raises(RuntimeError):
        engine.label_outcomes(1, "1d")
