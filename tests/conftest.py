"""Shared Phase-2 fixtures: calendar, synthetic provider, store, ingestion."""

import pandas as pd
import pytest

from algo.core.calendar import StaticCalendar
from algo.data.ingest import IngestionEngine
from algo.data.providers.synthetic import SyntheticDataProvider
from algo.data.store import MarketDataStore


@pytest.fixture
def calendar():
    days = pd.bdate_range("2024-01-01", "2024-06-30")
    return StaticCalendar([d.date() for d in days])


@pytest.fixture
def synthetic(calendar):
    return SyntheticDataProvider(seed=7, calendar=calendar)


@pytest.fixture
def store(tmp_path):
    return MarketDataStore(tmp_path / "mkt")


@pytest.fixture
def ingestion(store, synthetic, calendar):
    return IngestionEngine(store, synthetic, calendar)


@pytest.fixture
def symbols():
    return ["AAA", "BBB", "CCC"]
