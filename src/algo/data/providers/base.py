"""DataProvider interface - the single seam between the platform and any source.

The ingestion engine depends only on this contract, so NSE bhavcopy, a broker
API (Zerodha/Dhan/Upstox), a paid vendor, a CSV/parquet dump, or the synthetic
generator are fully interchangeable - the rest of the platform never knows which
source is underneath. This mirrors the (future) broker abstraction: swap the
implementation, change nothing downstream.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

import pandas as pd


class DataProvider(ABC):
    """Contract for fetching historical OHLCV bars."""

    #: Human-readable source identifier (recorded in ingestion runs).
    name: str = "provider"

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str, start, end) -> pd.DataFrame:
        """Return canonical OHLCV bars for ``symbol``/``timeframe`` in the
        inclusive ``[start, end]`` window (may be empty). Implementations should
        return a frame that ``algo.data.ohlcv.normalize`` accepts.
        """

    def list_symbols(self) -> List[str]:
        """Symbols the provider can serve, if known (else empty)."""
        return []
