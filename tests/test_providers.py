"""Data providers: synthetic determinism/session-awareness, CSV import."""

import pandas as pd

from algo.data.providers.csv_provider import CsvDataProvider
from algo.data.providers.nse import NseProvider
import pytest


def test_synthetic_deterministic(synthetic):
    a = synthetic.fetch_ohlcv("XYZ", "1d", "2024-01-01", "2024-02-01")
    b = synthetic.fetch_ohlcv("XYZ", "1d", "2024-01-01", "2024-02-01")
    pd.testing.assert_frame_equal(a, b)
    # different symbols -> different paths
    c = synthetic.fetch_ohlcv("ABC", "1d", "2024-01-01", "2024-02-01")
    assert not a["close"].equals(c["close"])


def test_synthetic_session_aware(synthetic, calendar):
    df = synthetic.fetch_ohlcv("XYZ", "1d", "2024-01-01", "2024-01-31")
    sessions = calendar.sessions("2024-01-01", "2024-01-31")
    assert len(df) == len(sessions)
    assert {d.date() for d in df["date"]} <= set(sessions)
    assert df["date"].is_monotonic_increasing and df["date"].is_unique


def test_synthetic_intraday_within_session(calendar):
    from algo.data.providers.synthetic import SyntheticDataProvider
    prov = SyntheticDataProvider(calendar=calendar)
    df = prov.fetch_ohlcv("XYZ", "15m", "2024-01-02", "2024-01-02")
    # 09:15 -> 15:30 = 375 min / 15 = 25 bars
    assert len(df) == 25
    assert df["date"].dt.hour.min() == 9


def test_csv_provider_round_trip(tmp_path):
    root = tmp_path / "csv"
    (root / "1d").mkdir(parents=True)
    pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=3, tz="UTC"),
        "open": [1, 2, 3], "high": [2, 3, 4], "low": [0.5, 1.5, 2.5],
        "close": [1.5, 2.5, 3.5], "volume": [100, 200, 300],
    }).to_csv(root / "1d" / "AAA.csv", index=False)
    prov = CsvDataProvider(root)
    df = prov.fetch_ohlcv("AAA", "1d", "2024-01-01", "2024-01-31")
    assert len(df) == 3
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert prov.list_symbols() == ["AAA"]
    assert prov.fetch_ohlcv("ZZZ", "1d", "2024-01-01", "2024-01-31").empty


def test_nse_provider_is_a_wiring_stub():
    with pytest.raises(NotImplementedError):
        NseProvider().fetch_ohlcv("RELIANCE", "1d", "2024-01-01", "2024-01-31")
