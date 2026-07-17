"""Angel One SmartAPI market-data provider - the PRODUCTION NSE source (Phase 6).

Implements the existing ``DataProvider`` interface against the official
SmartAPI Python SDK (``smartapi-python``, class ``SmartConnect``), so it drops
straight into the ingestion / store / scheduler / scanner / research layers
with no changes elsewhere. Supersedes the Kotak Neo provider as the production
source (Kotak code remains in-tree but unused - D-020).

Components:
    config       SmartApiConfig      - credentials from .env/environment ONLY,
                                       secret fields masked, clear messages.
    session      SmartApiSession     - login (TOTP), refresh, logout, profile.
    instruments  SmartApiInstruments - official scrip master JSON -> symbol/token
                                       map, parquet cache.
    provider     SmartApiDataProvider- historical candles (chunked + throttled
                                       per official limits) and latest quote.

Everything is dependency-injectable; the SDK and pyotp are OPTIONAL imports
needed only for a real authenticated session. The full pipeline is developed
and tested without credentials (Task 4); with a local .env it works unchanged
(Task 5).
"""

from algo.data.providers.smartapi.config import SmartApiConfig, SmartApiConfigError
from algo.data.providers.smartapi.instruments import SmartApiInstruments
from algo.data.providers.smartapi.provider import SmartApiDataProvider
from algo.data.providers.smartapi.session import SmartApiAuthError, SmartApiSession


def build_provider(cache_dir=None, env_file: str = ".env",
                   evidence_logger=None) -> SmartApiDataProvider:
    """One-call production wiring: .env -> config -> session -> instruments ->
    provider. Nothing authenticates until the first request needs it."""
    config = SmartApiConfig.from_env(env_file=env_file)
    session = SmartApiSession(config)
    instruments = SmartApiInstruments(config.instruments_url,
                                      cache_dir=cache_dir,
                                      evidence_logger=evidence_logger)
    return SmartApiDataProvider(session, instruments)


__all__ = [
    "SmartApiConfig", "SmartApiConfigError", "SmartApiSession",
    "SmartApiAuthError", "SmartApiInstruments", "SmartApiDataProvider",
    "build_provider",
]
