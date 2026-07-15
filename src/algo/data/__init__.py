"""Market-data layer: providers, OHLCV store, quality gates, ingestion.

Source-agnostic by design: the ``IngestionEngine`` talks only to the
``DataProvider`` interface, so NSE bhavcopy, a broker API, a vendor dump, or a
CSV export are interchangeable. Candles are stored in parquet (never in the
evidence SQLite - decision D-010). Phase 2 delivers the whole pipeline plus
offline providers (synthetic, CSV); the live NSE/broker provider is a documented
wiring point filled once the data source is chosen.
"""
