"""Data-layer extensions: index instruments, manifest, store audit.

Phase 1 of the data-infrastructure implementation (DATA_INFRASTRUCTURE_PLAN).
No network: the instrument master is injected; audit/manifest run on synthetic
stores.
"""

import json

import numpy as np
import pandas as pd
import pytest

from algo.data import manifest
from algo.data.audit import (
    audit_timeframe, context_completeness, cross_timeframe,
)
from algo.data.ingest import IngestionReport, SymbolResult
from algo.data.providers.smartapi.instruments import (
    INDEX_SYMBOLS, SmartApiInstruments,
)
from algo.data.store import MarketDataStore

MASTER = [
    {"token": "3045", "symbol": "SBIN-EQ", "name": "SBIN", "expiry": "",
     "strike": "-1.0", "lotsize": "1", "instrumenttype": "",
     "exch_seg": "NSE", "tick_size": "5.0"},
    {"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE",
     "expiry": "", "strike": "-1.0", "lotsize": "1", "instrumenttype": "",
     "exch_seg": "NSE", "tick_size": "5.0"},
    {"token": "99926000", "symbol": "Nifty 50", "name": "NIFTY",
     "expiry": "", "strike": "-1.0", "lotsize": "1",
     "instrumenttype": "AMXIDX", "exch_seg": "NSE", "tick_size": "0.0"},
    {"token": "99926009", "symbol": "Nifty Bank", "name": "BANKNIFTY",
     "expiry": "", "strike": "-1.0", "lotsize": "1",
     "instrumenttype": "AMXIDX", "exch_seg": "NSE", "tick_size": "0.0"},
    {"token": "99926037", "symbol": "India VIX", "name": "INDIA VIX",
     "expiry": "", "strike": "-1.0", "lotsize": "1",
     "instrumenttype": "AMXIDX", "exch_seg": "NSE", "tick_size": "0.0"},
]


def _instruments(records=MASTER, cache_dir=None):
    return SmartApiInstruments("http://test", cache_dir=cache_dir,
                               downloader=lambda url: json.dumps(records))


# ------------------------------------------------------------- instruments

def test_index_master_resolves_canonical_symbols_and_tokens():
    inst = _instruments()
    idx = inst.fetch_indices()
    assert sorted(idx["symbol"]) == sorted(INDEX_SYMBOLS)
    assert inst.token_for("NIFTY50") == "99926000"
    assert inst.token_for("BANKNIFTY") == "99926009"
    assert inst.token_for("INDIAVIX") == "99926037"
    assert inst.tradingsymbol_for("NIFTY50") == "Nifty 50"


def test_equity_master_is_unpolluted_by_indices():
    inst = _instruments()
    inst.fetch()
    inst.fetch_indices()
    assert inst.symbols() == ["RELIANCE", "SBIN"]     # equities only
    assert inst.token_for("RELIANCE") == "2885"       # equity path intact


def test_missing_index_is_skipped_not_fatal():
    no_vix = [r for r in MASTER if r["symbol"] != "India VIX"]
    inst = _instruments(no_vix)
    idx = inst.fetch_indices()
    assert "INDIAVIX" not in set(idx["symbol"])
    assert inst.token_for("INDIAVIX") is None         # documented, no raise
    assert inst.token_for("NIFTY50") == "99926000"


def test_index_lookup_is_lazy_and_cached(tmp_path):
    inst = _instruments(cache_dir=tmp_path)
    inst.fetch()
    # no explicit ensure_indices: token_for must resolve lazily
    assert inst.token_for("BANKNIFTY") == "99926009"
    # a fresh instance with a FAILING downloader must resolve from cache
    fresh = SmartApiInstruments(
        "http://test", cache_dir=tmp_path,
        downloader=lambda url: (_ for _ in ()).throw(RuntimeError("offline")))
    assert fresh.token_for("NIFTY50") == "99926000"


def test_unknown_symbol_still_returns_none():
    inst = _instruments()
    inst.fetch()
    assert inst.token_for("NOSUCH") is None


# ---------------------------------------------------------------- manifest

def _bars(day: str, n: int, freq: str = "15min") -> pd.DataFrame:
    dates = pd.date_range(f"{day} 03:45", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({
        "date": dates, "open": 100.0, "high": 101.0, "low": 99.0,
        "close": 100.5, "volume": 1000.0})


def test_manifest_records_events_and_skips_noise(tmp_path):
    store = MarketDataStore(tmp_path)
    store.write("RELIANCE", "15m", _bars("2024-03-04", 25))
    report = IngestionReport(timeframe="15m", results=[
        SymbolResult("RELIANCE", "15m", "backfilled", fetched=25, added=25,
                     detail="head gap 2016-01-01 -> 2022-12-30"),
        SymbolResult("SBIN", "15m", "up_to_date"),
    ])
    path = manifest.record(store, report, provider="smartapi")
    data = json.loads(path.read_text())
    entry = data["RELIANCE"]["15m"]
    assert entry["rows"] == 25 and len(entry["events"]) == 1
    assert entry["events"][0]["op"] == "backfilled"
    assert data["SBIN"]["15m"]["events"] == []        # noise-free re-runs
    # second operation appends, never replaces
    manifest.record(store, IngestionReport(timeframe="15m", results=[
        SymbolResult("RELIANCE", "15m", "ok", fetched=5, added=5)]),
        provider="smartapi")
    data = json.loads(path.read_text())
    assert len(data["RELIANCE"]["15m"]["events"]) == 2


# ------------------------------------------------------------------- audit

def _build_store(tmp_path) -> MarketDataStore:
    """Two equities + NIFTY50, 4 sessions: one market-wide short (special),
    one symbol-specific short for B only (anomaly)."""
    store = MarketDataStore(tmp_path)
    days_full = ["2024-03-04", "2024-03-05", "2024-03-07"]
    special = "2024-03-06"
    for sym in ("AAA", "BBB", "NIFTY50"):
        frames = [_bars(d, 25) for d in days_full] + [_bars(special, 10)]
        if sym == "BBB":                       # symbol-specific short session
            frames[1] = _bars(days_full[1], 12)
        store.write(sym, "15m", pd.concat(frames, ignore_index=True))
    return store


def test_audit_classifies_special_sessions_and_symbol_anomalies(tmp_path):
    store = _build_store(tmp_path)
    r = audit_timeframe(store, "15m")
    assert r["symbols"] == 3 and r["union_sessions"] == 4
    assert r["special_sessions"] == ["2024-03-06"]    # market-wide short
    assert any("BBB" in w and "short session" in w for w in r["warnings"])
    assert not any("AAA" in f for f in r["failures"])


def test_audit_flags_missing_sessions_post_listing(tmp_path):
    store = MarketDataStore(tmp_path)
    days = [f"2024-03-{d:02d}" for d in (4, 5, 6, 7, 11, 12, 13, 14)]
    store.write("AAA", "15m", pd.concat([_bars(d, 25) for d in days],
                                        ignore_index=True))
    missing_many = pd.concat([_bars(days[0], 25), _bars(days[-1], 25)],
                             ignore_index=True)
    store.write("BBB", "15m", missing_many)           # 6 missing sessions
    r = audit_timeframe(store, "15m")
    assert any("BBB" in f and "missing sessions" in f for f in r["failures"])


def test_context_completeness_requires_index_bars(tmp_path):
    store = _build_store(tmp_path)
    failures, warnings = context_completeness(store, "15m")
    assert failures == []                              # NIFTY50 covers all
    # drop NIFTY50's last session -> uncovered equity session = failure
    nifty = store.read("NIFTY50", "15m")
    clipped = nifty[nifty["date"] < pd.Timestamp("2024-03-07", tz="UTC")]
    store._path("NIFTY50", "15m").unlink()
    store.write("NIFTY50", "15m", clipped)
    failures, warnings = context_completeness(store, "15m")
    assert failures and "lack NIFTY50 bars" in failures[0]


def test_context_absence_is_a_warning_not_failure(tmp_path):
    store = MarketDataStore(tmp_path)
    store.write("AAA", "15m", _bars("2024-03-04", 25))
    failures, warnings = context_completeness(store, "15m")
    assert failures == []
    assert warnings and "no NIFTY50" in warnings[0]


def test_cross_timeframe_tolerates_small_depth_differences(tmp_path):
    store = MarketDataStore(tmp_path)
    days = [f"2024-03-{d:02d}" for d in (4, 5, 6, 7, 11)]
    store.write("AAA", "15m", pd.concat([_bars(d, 25) for d in days],
                                        ignore_index=True))
    store.write("AAA", "5m", pd.concat(
        [_bars(d, 75, "5min") for d in days], ignore_index=True))
    assert cross_timeframe(store, "5m", "15m") == []
    # BBB: 5m missing 4 of the overlapping sessions -> flagged
    store.write("BBB", "15m", pd.concat([_bars(d, 25) for d in days],
                                        ignore_index=True))
    store.write("BBB", "5m", _bars(days[0], 75, "5min"))
    notes = cross_timeframe(store, "5m", "15m")
    assert notes and "BBB" in notes[0]
