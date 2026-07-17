"""Data providers - interchangeable market-data sources behind one interface."""

from algo.data.providers.base import DataProvider
from algo.data.providers.csv_provider import CsvDataProvider
from algo.data.providers.synthetic import SyntheticDataProvider
from algo.data.providers.kotak import (
    KotakNeoConfig, KotakNeoDataProvider, KotakNeoInstruments, KotakNeoSession,
)
from algo.data.providers.smartapi import (
    SmartApiConfig, SmartApiDataProvider, SmartApiInstruments, SmartApiSession,
)

__all__ = [
    "DataProvider", "CsvDataProvider", "SyntheticDataProvider",
    # production NSE source (Phase 6)
    "SmartApiConfig", "SmartApiSession", "SmartApiInstruments",
    "SmartApiDataProvider",
    # superseded, retained (D-020)
    "KotakNeoConfig", "KotakNeoSession", "KotakNeoInstruments",
    "KotakNeoDataProvider",
]
