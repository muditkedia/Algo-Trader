"""IngestionEngine: full import, incremental, idempotency, quarantine."""

import pandas as pd

from algo.data.ingest import IngestionEngine
from algo.data.providers.base import DataProvider


def test_full_import_stores_all(ingestion, store, symbols):
    report = ingestion.full_import(symbols, "1d", "2024-01-01", "2024-03-31")
    assert report.summary()["by_status"].get("ok") == len(symbols)
    for sym in symbols:
        assert store.coverage(sym, "1d")["rows"] > 0


def test_incremental_advances_then_idempotent(ingestion, store, symbols):
    ingestion.full_import(symbols, "1d", "2024-01-01", "2024-02-15")
    before = {s: store.coverage(s, "1d")["rows"] for s in symbols}

    # advance to a later end -> new rows appended
    ingestion.incremental_update(symbols, "1d", end="2024-03-29")
    mid = {s: store.coverage(s, "1d")["rows"] for s in symbols}
    assert all(mid[s] > before[s] for s in symbols)

    # re-run to same end -> NO new rows (no duplicate downloads)
    report = ingestion.incremental_update(symbols, "1d", end="2024-03-29")
    assert all(r.status in ("up_to_date", "empty") for r in report.results)
    after = {s: store.coverage(s, "1d")["rows"] for s in symbols}
    assert after == mid


def test_incremental_from_empty(ingestion, store, symbols):
    report = ingestion.incremental_update(
        symbols, "1d", end="2024-02-15", start_if_empty="2024-01-01")
    assert all(r.status == "ok" for r in report.results)
    assert all(store.coverage(s, "1d")["rows"] > 0 for s in symbols)


class _BadProvider(DataProvider):
    name = "bad"

    def fetch_ohlcv(self, symbol, timeframe, start, end):
        # high < low and high < body -> quality errors
        return pd.DataFrame({
            "date": [pd.Timestamp("2024-01-01", tz="UTC")],
            "open": [10.0], "high": [5.0], "low": [8.0], "close": [9.0],
            "volume": [100.0]})


def test_bad_data_is_quarantined_not_stored(store, calendar):
    engine = IngestionEngine(store, _BadProvider(), calendar)
    report = engine.full_import(["AAA"], "1d", "2024-01-01", "2024-01-02")
    assert report.results[0].status == "quarantined"
    assert report.results[0].quality is not None
    assert store.coverage("AAA", "1d") is None  # nothing admitted
