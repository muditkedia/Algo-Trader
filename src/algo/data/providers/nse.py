"""NseProvider - generic NSE wiring-point stub (superseded by Kotak Neo).

The concrete NSE market-data provider is now
``algo.data.providers.kotak.KotakNeoDataProvider`` (Phase 3), which implements
this same ``DataProvider`` interface against the official Kotak Neo SDK. This
stub is retained only as a template for adding OTHER NSE sources (a second
broker, a vendor) later.

Wiring any live feed requires two owner decisions that cannot be handled here:

  1. The data source (NSE bhavcopy archives, or a broker API - Zerodha Kite,
     Dhan, Upstox - or a paid vendor). Each has different auth, rate limits,
     symbol conventions, and adjustment semantics.
  2. Credentials. API keys / tokens must be supplied by the owner through the
     environment; they are never entered or stored by the assistant.

Because the platform is source-agnostic (everything depends only on
``DataProvider``), filling this in later changes nothing downstream: implement
``fetch_ohlcv`` to return a frame that ``algo.data.ohlcv.normalize`` accepts and
hand an instance to the ``IngestionEngine``. The synthetic and CSV providers
already exercise the entire pipeline in the meantime.
"""

from __future__ import annotations

import pandas as pd

from algo.data.providers.base import DataProvider


class NseProvider(DataProvider):
    name = "nse"

    def __init__(self, *args, **kwargs) -> None:  # accept future config
        self._config = kwargs

    def fetch_ohlcv(self, symbol: str, timeframe: str, start, end) -> pd.DataFrame:
        raise NotImplementedError(
            "NseProvider is a wiring point, not yet implemented. Choose a data "
            "source (bhavcopy / Zerodha / Dhan / Upstox / vendor), supply "
            "credentials via environment variables, and implement fetch_ohlcv to "
            "return a canonical OHLCV frame. The pipeline is source-agnostic, so "
            "no other module changes. Use CsvDataProvider to import exports today."
        )
