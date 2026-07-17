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


class _OneGlitchProvider(DataProvider):
    """Clean history with a single invalid bar - the real SmartAPI pattern
    (observed: ~1 bad bar per 20,000)."""
    name = "glitch"

    def fetch_ohlcv(self, symbol, timeframe, start, end):
        # 1 bad bar in 2000 = 0.05%, the real observed order of magnitude
        # (SmartAPI: BRITANNIA 1/21829 = 0.005%, CANBK 4/21828 = 0.018%)
        n = 2000
        dates = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
        close = pd.Series(100.0, index=range(n))
        frame = pd.DataFrame({
            "date": dates, "open": close, "high": close + 1.0,
            "low": close - 1.0, "close": close, "volume": 1000.0})
        frame.loc[250, "low"] = 105.0        # low > open/close: impossible bar
        return frame


def test_isolated_glitch_bar_is_dropped_not_the_whole_symbol(store, calendar):
    engine = IngestionEngine(store, _OneGlitchProvider(), calendar,
                             max_invalid_row_pct=0.001)
    report = engine.full_import(["AAA"], "15m", "2024-01-01", "2024-12-31")
    result = report.results[0]
    assert result.status == "ok"                  # 1999 good bars survive
    assert "dropped 1 invalid bar" in result.detail   # explicit, never silent
    assert store.coverage("AAA", "15m")["rows"] == 1999


def test_strict_mode_still_rejects_everything(store, calendar):
    engine = IngestionEngine(store, _OneGlitchProvider(), calendar,
                             max_invalid_row_pct=0.0,
                             max_invalid_rows=0)           # all-or-nothing
    report = engine.full_import(["AAA"], "15m", "2024-01-01", "2024-12-31")
    assert report.results[0].status == "quarantined"
    assert store.coverage("AAA", "15m") is None


def test_absolute_floor_saves_short_daily_series(store, calendar):
    """One bad bar in an 877-row daily history is 0.11% - a percentage-only
    rule would reject the symbol, though the defect is a single glitch."""
    class ShortDaily(DataProvider):
        name = "short"

        def fetch_ohlcv(self, symbol, timeframe, start, end):
            n = 877
            dates = pd.bdate_range("2023-01-01", periods=n, tz="UTC")
            close = pd.Series(100.0, index=range(n))
            frame = pd.DataFrame({
                "date": dates, "open": close, "high": close + 1.0,
                "low": close - 1.0, "close": close, "volume": 1000.0})
            frame.loc[400, "low"] = 105.0
            return frame

    engine = IngestionEngine(store, ShortDaily(), calendar,
                             max_invalid_row_pct=0.001, max_invalid_rows=5)
    report = engine.full_import(["AAA"], "1d", "2023-01-01", "2026-07-01")
    assert report.results[0].status == "ok"
    assert store.coverage("AAA", "1d")["rows"] == 876


def test_systematically_broken_feed_is_still_quarantined(store, calendar):
    engine = IngestionEngine(store, _BadProvider(), calendar,
                             max_invalid_row_pct=0.001)
    # 100% of rows bad -> far above tolerance -> refuse the symbol
    report = engine.full_import(["AAA"], "1d", "2024-01-01", "2024-01-02")
    assert report.results[0].status == "quarantined"
    assert store.coverage("AAA", "1d") is None
