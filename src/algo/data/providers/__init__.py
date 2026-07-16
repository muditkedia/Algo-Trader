"""Data providers - interchangeable market-data sources behind one interface."""

from algo.data.providers.base import DataProvider
from algo.data.providers.csv_provider import CsvDataProvider
from algo.data.providers.synthetic import SyntheticDataProvider
from algo.data.providers.kotak import (
    KotakNeoConfig, KotakNeoDataProvider, KotakNeoInstruments, KotakNeoSession,
)

__all__ = [
    "DataProvider", "CsvDataProvider", "SyntheticDataProvider",
    "KotakNeoConfig", "KotakNeoSession", "KotakNeoInstruments",
    "KotakNeoDataProvider",
]
