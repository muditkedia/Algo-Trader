"""Scheduling jobs: full import, daily update, intraday refresh (idempotent)."""

from algo.schedule.jobs import DataJobs


def test_full_import_job(ingestion, store, symbols, calendar):
    jobs = DataJobs(ingestion, calendar)
    report = jobs.full_import(symbols, "1d", "2024-01-01", "2024-02-29")
    assert report.summary()["by_status"].get("ok") == len(symbols)
    assert all(store.coverage(s, "1d")["rows"] > 0 for s in symbols)


def test_daily_update_advances_then_idempotent(ingestion, store, symbols, calendar):
    jobs = DataJobs(ingestion, calendar)
    jobs.full_import(symbols, "1d", "2024-01-01", "2024-02-15")
    before = {s: store.coverage(s, "1d")["rows"] for s in symbols}

    jobs.daily_update(symbols, "1d", as_of="2024-02-29")
    mid = {s: store.coverage(s, "1d")["rows"] for s in symbols}
    assert all(mid[s] > before[s] for s in symbols)

    report = jobs.daily_update(symbols, "1d", as_of="2024-02-29")
    assert all(r.status in ("up_to_date", "empty") for r in report.results)
    after = {s: store.coverage(s, "1d")["rows"] for s in symbols}
    assert after == mid


def test_intraday_refresh_incremental(store, calendar):
    from algo.data.ingest import IngestionEngine
    from algo.data.providers.synthetic import SyntheticDataProvider
    provider = SyntheticDataProvider(calendar=calendar)
    jobs = DataJobs(IngestionEngine(store, provider, calendar), calendar)
    jobs.full_import(["AAA"], "5m", "2024-01-02", "2024-01-02")
    rows = store.coverage("AAA", "5m")["rows"]
    assert rows == 75  # one 375-min session at 5m = 375/5 bars
    # refresh with the same end -> nothing new
    report = jobs.intraday_refresh(["AAA"], "5m", as_of="2024-01-02T23:59:00")
    assert all(r.status in ("up_to_date", "empty") for r in report.results)
    assert store.coverage("AAA", "5m")["rows"] == rows
