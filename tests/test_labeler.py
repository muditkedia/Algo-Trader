"""Outcome Labeler: matured signals labeled exactly, immature deferred,
re-runs idempotent."""

import numpy as np
import pandas as pd
import pytest

from algo.core.costs import NseEquityCostModel
from algo.evidence.database import EvidenceDB, MEMORY
from algo.evidence.logger import EvidenceLogger
from algo.evidence.models import Signal
from algo.research.labeler import OutcomeLabeler


@pytest.fixture
def setup(store):
    closes = np.array([100.0 + i * 0.5 for i in range(40)])   # rising daily
    frame = pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=40, tz="UTC"),
        "open": closes, "high": closes + 1.0, "low": closes - 1.0,
        "close": closes, "volume": np.full(40, 100.0)})
    store.write("LAB", "1d", frame)

    db = EvidenceDB(MEMORY)
    log = EvidenceLogger(db)
    log.upsert_instrument("LAB")
    sid = log.register_strategy("labtest", "1.0")

    def record_at(bar_index):
        ts = str(pd.Timestamp(frame["date"].iloc[bar_index]))
        return log.record_signal(Signal(
            ts=ts, symbol="LAB", strategy_id=sid, direction="long",
            mode="backtest", disposition="recorded_only",
            entry_price=float(closes[bar_index]), confidence_score=0.5))

    labeler = OutcomeLabeler(store, log, NseEquityCostModel())
    yield store, db, log, sid, labeler, record_at, closes
    db.close()


def test_matured_signal_labeled_exactly(setup):
    store, db, log, sid, labeler, record_at, closes = setup
    signal_id = record_at(10)
    written = labeler.label_strategy(sid, "1d")
    assert written == 1
    row = db.connection.execute(
        "SELECT * FROM signal_outcomes WHERE signal_id=?",
        (signal_id,)).fetchone()
    entry = closes[10]
    assert row["ret_1d"] == pytest.approx(closes[11] / entry - 1.0)
    assert row["ret_3d"] == pytest.approx(closes[13] / entry - 1.0)
    assert row["ret_5d"] == pytest.approx(closes[15] / entry - 1.0)
    # next 8 bars: highs = close+1, lows = close-1
    assert row["mfe_pct"] == pytest.approx((closes[18] + 1.0) / entry - 1.0)
    assert row["mae_pct"] == pytest.approx((closes[11] - 1.0) / entry - 1.0)
    assert row["est_cost_pct"] > 0
    assert row["cost_model_version"] == "NseEquityCostModel"
    assert row["sim_exit_reason"] is not None
    assert row["sim_pnl_net"] is not None
    assert row["overnight_gap_pct"] is not None


def test_immature_signal_deferred_and_idempotent(setup):
    store, db, log, sid, labeler, record_at, closes = setup
    record_at(10)                    # matured
    record_at(38)                    # too close to the end - cannot mature
    assert labeler.label_strategy(sid, "1d") == 1
    assert labeler.label_strategy(sid, "1d") == 0        # idempotent re-run
    n = db.connection.execute(
        "SELECT COUNT(*) FROM signal_outcomes").fetchone()[0]
    assert n == 1                                        # immature still pending
    assert len(labeler.pending(sid)) == 1
