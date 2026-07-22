"""Unified scan: registered intraday strategies, one ranked list,
evidence logging with components, and duplicate-signal protection."""

import json

import pandas as pd
import pytest

from algo.evidence.database import EvidenceDB, MEMORY
from algo.evidence.logger import EvidenceLogger
from algo.scanner.engine import ScanEngine
from algo.strategies.registry import StrategyRegistry
from algo.strategies.library import ALL_STRATEGIES

from tests.test_strategy_library import _vwap_frame
from tests.strat09_fixtures import strat09_frames
from tests.strat10_fixtures import strat10_frames
from strat01_fixtures import strat01_frames


AS_OF = pd.Timestamp("2024-03-05 16:00", tz="UTC")


@pytest.fixture
def scan_setup(store, synthetic):
    """Store: crafted firing frames plus synthetic context data."""
    orb = strat01_frames(symbol="CRAFT_ORB")
    store.write("CRAFT_ORB", "5m", orb["CRAFT_ORB"])
    store.write("CRAFT_VWAP", "5m", _vwap_frame())
    emacb = strat09_frames(symbol="CRAFT_EMACB")
    store.write("CRAFT_EMACB", "5m", emacb["CRAFT_EMACB"])
    gcc = strat10_frames(symbol="CRAFT_GCC")
    store.write("CRAFT_GCC", "5m", gcc["CRAFT_GCC"])
    nifty = (pd.concat([orb["NIFTY50"], emacb["NIFTY50"]])
             .sort_values("date").drop_duplicates("date", keep="first"))
    store.write("NIFTY50", "5m", nifty)
    for sym in ("PLAIN_A", "PLAIN_B"):     # synthetic, may or may not fire
        store.write(sym, "15m", synthetic.fetch_ohlcv(
            sym, "15m", "2023-06-01", "2024-03-05"))
    symbols = ["CRAFT_ORB", "CRAFT_VWAP", "CRAFT_EMACB", "CRAFT_GCC",
               "PLAIN_A", "PLAIN_B"]

    db = EvidenceDB(MEMORY)
    log = EvidenceLogger(db)
    for sym in symbols:
        log.upsert_instrument(sym)
    strategies = [cls() for cls in ALL_STRATEGIES]
    engine = ScanEngine(store, strategies, evidence_logger=log)
    yield engine, db, symbols
    db.close()


def test_registry_discovers_the_whole_library():
    reg = StrategyRegistry()
    found = reg.discover("algo.strategies.library")
    # the existing intraday strategies are always present; the library grows by discovery,
    # so assert membership + consistency rather than a hardcoded roster
    assert {"orb_5m",
            "ema_compression_5m", "geometric_channel_5m", "pullback_15m", "volexp_1h",
            "vwap_trend_5m"} <= set(found)
    assert sorted(found) == [cls.meta.name for cls in ALL_STRATEGIES]
    assert reg.enabled_names() == sorted(found)


def test_unified_scan_returns_one_ranked_list(scan_setup):
    engine, db, symbols = scan_setup
    result = engine.scan(AS_OF, symbols)

    # every symbol with data was processed; crafted setups fired
    assert result.n_symbols_with_data == len(symbols)
    fired = {(o.symbol, o.strategy) for o in result.opportunities}
    assert ("CRAFT_ORB", "orb_5m") in fired
    assert ("CRAFT_VWAP", "vwap_trend_5m") in fired
    assert ("CRAFT_EMACB", "ema_compression_5m") in fired
    assert ("CRAFT_GCC", "geometric_channel_5m") in fired

    # ONE list, strictly ranked 1..N, ordered by confidence descending
    ranks = [o.rank for o in result.opportunities]
    assert ranks == list(range(1, len(ranks) + 1))
    confidences = [o.confidence for o in result.opportunities]
    assert confidences == sorted(confidences, reverse=True)
    assert all(0.0 <= c <= 1.0 for c in confidences)
    assert {o.strategy for o in result.opportunities} >= {
        "ema_compression_5m", "geometric_channel_5m", "orb_5m",
        "vwap_trend_5m"}


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
    # crafted ORB signal carries its full component breakdown PLUS the
    # persisted ranking decision (every ranking decision is auditable)
    orb = by_key[("CRAFT_ORB", "orb_5m")]
    components = json.loads(orb["confidence_components"])
    assert {"specification_score", "regime", "_ranking"} <= set(components)
    assert all(0.0 <= c["score"] <= 1.0
               for name, c in components.items() if name != "_ranking")


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
    orb = strat01_frames(symbol="CRAFT_ORB")
    store.write("CRAFT_ORB", "5m", orb["CRAFT_ORB"])
    store.write("NIFTY50", "5m", orb["NIFTY50"])
    engine = ScanEngine(store, [cls() for cls in ALL_STRATEGIES])
    result = engine.scan(AS_OF, ["CRAFT_ORB"])
    assert any(o.strategy == "orb_5m" for o in result.opportunities)
