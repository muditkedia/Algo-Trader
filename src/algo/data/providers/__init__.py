"""Data providers - interchangeable market-data sources behind one interface."""

from algo.data.providers.base import DataProvider
from algo.data.providers.csv_provider import CsvDataProvider
from algo.data.providers.synthetic import SyntheticDataProvider

__all__ = ["DataProvider", "CsvDataProvider", "SyntheticDataProvider"]
