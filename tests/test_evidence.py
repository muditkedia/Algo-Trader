"""Evidence database + logger: schema, lineage, and the record-everything path."""

import json

import pytest

from algo.evidence.database import EvidenceDB, MEMORY
from algo.evidence import schema as schema_mod
from algo.evidence.logger import EvidenceLogger
from algo.evidence.models import (
    Direction, Disposition, Mode, Signal, SignalOutcome, StrategyStatus,
    TradeRecord,
)


@pytest.fixture
def db():
    database = EvidenceDB(MEMORY)
    yield database
    database.close()


def test_schema_installed(db):
    assert db.schema_version == schema_mod.SCHEMA_VERSION == 1
    assert set(db.tables()) == set(schema_mod.TABLES)


def test_initialize_is_idempotent(db):
    db.initialize()  # second call must not raise or duplicate
    assert set(db.tables()) == set(schema_mod.TABLES)


def test_strategy_registration_idempotent_and_status_events(db):
    log = EvidenceLogger(db)
    sid = log.register_strategy("orb", "1.0")
    again = log.register_strategy("orb", "1.0")
    assert sid == again  # (name, version) unique -> same id

    log.set_strategy_status(sid, StrategyStatus.MEASURED.value, reason="passed edge")
    row = db.connection.execute(
        "SELECT status, status_reason FROM strategies WHERE strategy_id=?",
        (sid,)).fetchone()
    assert row["status"] == "measured"
    events = db.connection.execute(
        "SELECT to_status FROM strategy_events WHERE strategy_id=? ORDER BY event_id",
        (sid,)).fetchall()
    assert [e["to_status"] for e in events] == ["draft", "measured"]


def test_record_signal_unconditional_with_context(db):
    log = EvidenceLogger(db)
    log.upsert_instrument("RELIANCE", sector="Energy")
    sid = log.register_strategy("orb", "1.0")
    run = log.start_run("scan")

    components = {"trend": {"score": 1.0, "weight": 0.5}}
    signal = Signal(
        ts="2024-06-03T09:20:00+00:00", symbol="RELIANCE", strategy_id=sid,
        direction=Direction.LONG.value, mode=Mode.BACKTEST.value,
        disposition=Disposition.REJECTED.value,
        disposition_reason="below_confidence_floor", run_id=run.run_id,
        entry_price=2900.0, atr_pct=0.012, trend_regime="bull",
        confidence_score=0.41, confidence_components=components,
    )
    signal_id = log.record_signal(signal)
    assert signal_id > 0

    stored = db.connection.execute(
        "SELECT * FROM signals WHERE signal_id=?", (signal_id,)).fetchone()
    assert stored["disposition"] == "rejected"
    assert stored["run_id"] == run.run_id
    # JSON round-trip of the confidence components
    assert json.loads(stored["confidence_components"]) == components


def test_outcome_trade_evaluation_regime(db):
    log = EvidenceLogger(db)
    log.upsert_instrument("TCS")
    sid = log.register_strategy("orb", "1.0")
    sig_id = log.record_signal(Signal(
        ts="2024-06-03T09:20:00+00:00", symbol="TCS", strategy_id=sid,
        direction="long", mode="backtest", disposition="executed"))

    log.record_outcome(SignalOutcome(signal_id=sig_id, ret_60m=0.004,
                                     mfe_pct=0.009, mae_pct=-0.003))
    out = db.connection.execute(
        "SELECT ret_60m, labeled_at FROM signal_outcomes WHERE signal_id=?",
        (sig_id,)).fetchone()
    assert out["ret_60m"] == pytest.approx(0.004)
    assert out["labeled_at"] is not None

    tid = log.record_trade(TradeRecord(
        mode="backtest", symbol="TCS", open_date="2024-06-03T09:20:00+00:00",
        close_date="2024-06-03T10:20:00+00:00", profit_ratio=0.004,
        profit_abs=40.0, stake_amount=10000.0, signal_id=sig_id,
        strategy_id=sid))
    assert tid > 0

    eid = log.record_evaluation(sid, "overall", "all", n_trades=1,
                                net_expectancy=0.004, profit_factor=1.5)
    assert eid > 0

    log.record_regime("2024-06-03", "NIFTY50", trend_label="bull",
                      vol_label="normal_volatility")
    reg = db.connection.execute(
        "SELECT trend_label FROM regime_daily WHERE date=? AND symbol=?",
        ("2024-06-03", "NIFTY50")).fetchone()
    assert reg["trend_label"] == "bull"


def test_foreign_key_enforced(db):
    log = EvidenceLogger(db)
    sid = log.register_strategy("orb", "1.0")
    # symbol 'GHOST' was never inserted into instruments -> FK violation
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        log.record_signal(Signal(
            ts="2024-06-03T09:20:00+00:00", symbol="GHOST", strategy_id=sid,
            direction="long", mode="backtest", disposition="rejected"))
