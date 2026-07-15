"""MarketDataStore: round-trip, upsert dedup, incremental, coverage."""

import pandas as pd


def test_round_trip_and_dedup(store, synthetic):
    df = synthetic.fetch_ohlcv("AAA", "1d", "2024-01-01", "2024-03-31")
    added = store.write("AAA", "1d", df)
    assert added == len(df) > 0

    back = store.read("AAA", "1d")
    assert len(back) == len(df)
    assert back["date"].is_unique and back["date"].is_monotonic_increasing

    # writing the identical frame again adds nothing (duplicate protection)
    assert store.write("AAA", "1d", df) == 0
    assert len(store.read("AAA", "1d")) == len(df)


def test_incremental_overlap_no_duplicates(store, synthetic):
    a = synthetic.fetch_ohlcv("AAA", "1d", "2024-01-01", "2024-02-15")
    b = synthetic.fetch_ohlcv("AAA", "1d", "2024-02-01", "2024-03-31")  # overlaps
    store.write("AAA", "1d", a)
    store.write("AAA", "1d", b)
    back = store.read("AAA", "1d")
    assert back["date"].is_unique
    union = pd.concat([a, b]).drop_duplicates("date")
    assert len(back) == len(union)


def test_coverage_and_discovery(store, synthetic):
    assert store.coverage("AAA", "1d") is None
    df = synthetic.fetch_ohlcv("AAA", "1d", "2024-01-01", "2024-03-31")
    store.write("AAA", "1d", df)
    cov = store.coverage("AAA", "1d")
    assert cov["rows"] == len(df)
    assert cov["start"] == df["date"].iloc[0] and cov["end"] == df["date"].iloc[-1]
    assert store.last_date("AAA", "1d") == df["date"].iloc[-1]
    assert store.symbols("1d") == ["AAA"]
    assert "1d" in store.timeframes()


def test_read_range_and_tail(store, synthetic):
    df = synthetic.fetch_ohlcv("AAA", "1d", "2024-01-01", "2024-03-31")
    store.write("AAA", "1d", df)
    clipped = store.read("AAA", "1d", start="2024-02-01", end="2024-02-29")
    assert clipped["date"].min() >= pd.Timestamp("2024-02-01", tz="UTC")
    assert clipped["date"].max() <= pd.Timestamp("2024-02-29", tz="UTC")
    assert len(store.tail("AAA", "1d", 5)) == 5
