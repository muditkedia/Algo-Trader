# Project State

_Last updated: 2026-07-15_

## Current Phase

**Equities pivot — Phase 3 (Kotak Neo market-data provider) COMPLETE, uncommitted.**
Phase 1 (`4f4d630`) and Phase 2 (`5a14fd6`) are committed and pushed.

A research-first platform for Indian equities. Phase 3 replaced the temporary NSE
stub with a real **Kotak Neo** data provider implementing the existing
`DataProvider` interface, so it drops into the Phase-2 ingestion/store/schedule/
scanner with no redesign. Still **no order placement, brokers-for-trading, or
strategies** — by design.

## ⚠️ Important finding — Kotak Neo SDK has no historical-candle API

Verified against the official SDK you supplied: the Kotak Neo v2 SDK exposes
**no historical OHLCV/candle endpoint**. OHLC is available only from
`quotes(quote_type='ohlc')` (the current session's bar) and the live websocket.
Consequences, handled honestly (no invented endpoints):

- `KotakNeoDataProvider.fetch_ohlcv` returns **today's daily bar** from the
  quotes snapshot. With the incremental scheduler this **accumulates a daily
  history going forward** ("incremental updates only") and is idempotent (the
  store upserts; a partial intraday snapshot is overwritten by the EOD bar).
- **Bulk backfill of past history** uses the existing `CsvDataProvider` (import a
  Kotak/vendor export) — or a future wiring of Kotak's separate charts/history
  endpoint if its official spec is provided.
- **Intraday bars** require the SDK's websocket feed (a live collector) — not
  served by this provider; such requests return empty.

## Phase 3 delivered (`src/algo/data/providers/kotak/`)

- `config.py` — `KotakNeoConfig`: all credentials from environment variables,
  secret fields masked (`repr=False`), TOTP via a live code or a pyotp secret.
- `session.py` — `KotakNeoSession`: the two-step TOTP login
  (`totp_login`→`totp_validate`), lazy optional SDK import, injectable
  `client_factory`; never hardcodes or logs secrets.
- `instruments.py` — `KotakNeoInstruments`: scrip-master download (records or
  `filesPaths` CSV), normalized symbol/token master, parquet cache, symbol→token
  lookup, optional evidence-table sync; injectable downloader.
- `provider.py` — `KotakNeoDataProvider(DataProvider)`: `fetch_ohlcv` via the
  quotes OHLC snapshot with a flexible response parser.
- Wired into `providers/__init__`; the old `NseProvider` now points here;
  `.env.example` rewritten for Kotak Neo variables.

## Reuse (no parallel implementations)

Reuses `DataProvider`, `IngestionEngine`, `MarketDataStore`, `DataJobs`,
`ScanEngine`, `EvidenceLogger`, `TradingCalendar`, the `ohlcv` helpers, and the
config pattern directly. The SDK (`neo_api_client`) and `pyotp` are optional,
lazily imported; no new hard dependency was added.

## Validation performed (mocked — real credentials unavailable, per the task)

- `pytest`: **77 passed** (64 prior + 13 Kotak). A `FakeNeoClient` injected via
  the session factory / instruments downloader covers: authentication (env creds,
  failure, missing-config), instrument loading (records + `filesPaths` CSV),
  OHLC download + response-shape flexibility, incremental ingestion + idempotency,
  scheduler `daily_update`, and scanner integration.
- Secret masking asserted (nothing sensitive in `repr`).

## Environment

- Python 3.11 venv, editable install. pandas 3.0.3 / numpy 2.4.6 / pyarrow 25.
- Optional at runtime for live Kotak use: `neo_api_client` (official SDK) and
  `pyotp` (only if using `KOTAK_NEO_TOTP_SECRET`). Neither is needed for tests.
- Credentials via environment (`.env`, git-ignored); see `.env.example`.

## Prior phases (committed)

- Phase 1 (`4f4d630`) — platform foundation: core, evidence DB + logger, strategy
  plugin interface + registry, universe framework, scanner interface, research
  skeleton, relocated validation package. Crypto code archived.
- Phase 2 (`5a14fd6`) — data & scanning layer: providers/store/quality/ingestion,
  universe management + filters, scanner engine, scheduling jobs.

## Next (Phase 4 — pending owner approval)

The research-engine port + first measured strategies (the D-007 gate). Live Kotak
data is a matter of installing the SDK and supplying `.env` credentials; the code
is ready and source-agnostic.

## Open decisions / actions needed from owner

- Approve committing Phase 3.
- To go live on Kotak data: `pip install neo_api_client pyotp`, fill `.env`, and
  run a daily `DataJobs.daily_update` after close (accumulates daily bars). For
  historical backfill, provide a CSV export or Kotak's charts-API spec.
- Authorize drafting `architecture/VALIDATION_RULES_EQ.md` (equity thresholds).

## Open blockers

- None for the code. Backfill depth is bounded by the SDK (see finding above);
  CSV import is the interim backfill path.
