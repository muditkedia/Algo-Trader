"""Unified scan: all six strategies, multiple timeframes, one ranked list,
evidence logging with components, and duplicate-signal protection."""

import json

import pandas as pd
import pytest

from algo.evidence.database import EvidenceDB, MEMORY
from algo.evidence.logger import EvidenceLogger
from algo.scanner.engine import ScanEngine
from algo.strategies.registry import StrategyRegistry
from algo.strategies.library import ALL_STRATEGIES

from tests.test_strategy_library import _nr7_frame, _orb_frame, _vwap_frame


AS_OF = pd.Timestamp("2024-03-05 16:00", tz="UTC")


@pytest.fixture
def scan_setup(store, synthetic):
    """Store: crafted firing frames for three symbols + synthetic context data."""
    store.write("CRAFT_ORB", "15m", _orb_frame())
    store.write("CRAFT_VWAP", "15m", _vwap_frame())
    store.write("CRAFT_NR7", "1d", _nr7_frame())
    for sym in ("PLAIN_A", "PLAIN_B"):     # synthetic, may or may not fire
        store.write(sym, "1d", synthetic.fetch_ohlcv(
            sym, "1d", "2023-06-01", "2024-03-05"))
    symbols = ["CRAFT_ORB", "CRAFT_VWAP", "CRAFT_NR7", "PLAIN_A", "PLAIN_B"]

    db = EvidenceDB(MEMORY)
    log = EvidenceLogger(db)
    for sym in symbols:
        log.upsert_instrument(sym)
    strategies = [cls() for cls in ALL_STRATEGIES]
    engine = ScanEngine(store, strategies, evidence_logger=log)
    yield engine, db, symbols
    db.close()


def test_registry_discovers_all_six():
    reg = StrategyRegistry()
    found = reg.discover("algo.strategies.library")
    assert sorted(found) == ["ema200_daily", "nr7_daily", "orb_15m",
                             "pullback_15m", "volexp_1h", "vwap_15m"]
    assert reg.enabled_names() == sorted(found)


def test_unified_scan_returns_one_ranked_list(scan_setup):
    engine, db, symbols = scan_setup
    result = engine.scan(AS_OF, symbols)

    # every symbol with data was processed; crafted setups fired
    assert result.n_symbols_with_data == len(symbols)
    fired = {(o.symbol, o.strategy) for o in result.opportunities}
    assert ("CRAFT_ORB", "orb_15m") in fired
    assert ("CRAFT_VWAP", "vwap_15m") in fired
    assert ("CRAFT_NR7", "nr7_daily") in fired

    # ONE list, strictly ranked 1..N, ordered by confidence descending
    ranks = [o.rank for o in result.opportunities]
    assert ranks == list(range(1, len(ranks) + 1))
    confidences = [o.confidence for o in result.opportunities]
    assert confidences == sorted(confidences, reverse=True)
    assert all(0.0 <= c <= 1.0 for c in confidences)
    # multiple timeframes merged into the same list
    assert {o.strategy for o in result.opportunities} >= {"orb_15m",
                                                          "nr7_daily"}


def test_every_opportunity_written_to_evidence(scan_setup):
    engine, db, symbols = scan_setup
    result = engine.scan(AS_OF, symbols)
    rows = db.connection.execute(
        "SELECT s.symbol, st.name AS strategy, s.confidence_score, "
        "s.confidence_components, s.disposition FROM signals s "
        "JOIN strategies st USING (strategy_id)").fetchall()
    assert len(rows) == len(result.opportunities) == result.n_candidates
    by_key = {(r["symbol"], r["strategy"]): r for r in rows}
    for opp in result.opportunities:
        row = by_key[(opp.symbol, opp.strategy)]
        assert row["disposition"] == "recorded_only"
        assert row["confidence_score"] == pytest.approx(opp.confidence)
    # crafted ORB signal carries its full component breakdown
    orb = by_key[("CRAFT_ORB", "orb_15m")]
    components = json.loads(orb["confidence_components"])
    assert set(components) == {"volume_surge", "range_tightness",
                               "close_strength"}
    assert all(0.0 <= c["score"] <= 1.0 for c in components.values())


def test_rescan_creates_no_duplicate_signals(scan_setup):
    engine, db, symbols = scan_setup
    first = engine.scan(AS_OF, symbols)
    count_after_first = db.connection.execute(
        "SELECT COUNT(*) FROM signals").fetchone()[0]
    assert count_after_first == first.n_candidates

    second = engine.scan(AS_OF, symbols)             # same bars re-observed
    count_after_second = db.connection.execute(
        "SELECT COUNT(*) FROM signals").fetchone()[0]
    assert count_after_second == count_after_first   # duplicate-protected
    # the scan itself still reports the (still-live) opportunities
    assert len(second.opportunities) == len(first.opportunities)


def test_scan_without_evidence_logger_still_ranks(store, synthetic):
    store.write("CRAFT_NR7", "1d", _nr7_frame())
    engine = ScanEngine(store, [cls() for cls in ALL_STRATEGIES])
    result = engine.scan(AS_OF, ["CRAFT_NR7"])
    assert any(o.strategy == "nr7_daily" for o in result.opportunities)
