"""Kotak Neo market-data provider - the concrete NSE source (Phase 3).

Implements the Phase-2 ``DataProvider`` interface against the official Kotak Neo
Python SDK (``neo_api_client``), so it drops into the existing ingestion, store,
scheduling, and scanner layers with no changes elsewhere.

Components:
    config       KotakNeoConfig  - env-var configuration (secrets masked).
    session      KotakNeoSession - secure TOTP login/session handling.
    instruments  KotakNeoInstruments - scrip-master download + symbol/token map.
    provider     KotakNeoDataProvider - DataProvider via the quotes OHLC snapshot.

IMPORTANT (verified against the supplied official SDK): the Kotak Neo v2 SDK
exposes NO historical-candle endpoint. OHLC is available only from
``quotes(quote_type='ohlc')`` (the current session's bar) and the live
websocket. This provider therefore accumulates DAILY bars going forward via the
incremental scheduler; bulk historical backfill is done with the existing
``CsvDataProvider`` (broker/vendor exports). See provider.py for detail.

The SDK is an OPTIONAL dependency imported lazily, and every collaborator is
dependency-injectable, so the whole package is unit-tested with mocks and needs
neither the SDK installed nor real credentials.
"""

from algo.data.providers.kotak.config import KotakNeoConfig
from algo.data.providers.kotak.instruments import KotakNeoInstruments
from algo.data.providers.kotak.provider import KotakNeoDataProvider
from algo.data.providers.kotak.session import KotakNeoSession, KotakAuthError

__all__ = [
    "KotakNeoConfig", "KotakNeoSession", "KotakAuthError",
    "KotakNeoInstruments", "KotakNeoDataProvider",
]
