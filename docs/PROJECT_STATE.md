# Project State

_Last updated: 2026-07-15_

## Current Phase

**Equities pivot — Phase 2 (data & scanning layer) COMPLETE, uncommitted.**
Phase 1 (platform foundation) is committed and pushed (`4f4d630`).

The project is a research-first quantitative platform for **Indian equities**.
Phase 2 built the complete market-data and scanning layer on top of the Phase 1
foundation. Still **no brokers, no paper/live trading, no strategies** — by
design. Everything in Phase 2 is in the working tree, awaiting approval to commit.

## Phase 2 delivered (`src/algo/data`, `universe`, `scanner`, `schedule`)

- **data/** — the source-agnostic market-data layer:
  - `ohlcv` canonical bar contract (+ window/timezone helpers);
  - `providers/` — `DataProvider` interface with `SyntheticDataProvider`
    (deterministic, session-aware) and `CsvDataProvider` (bhavcopy/broker/vendor
    imports) working offline, plus `NseProvider` — a documented wiring point for
    a live feed (needs the owner's source choice + credentials);
  - `store` — `MarketDataStore`, parquet per symbol×timeframe, upsert-dedup
    (candles never in SQLite, D-010);
  - `quality` — data-quality gates adapted from the archived `verify_data`
    (errors block, warnings admit; calendar-aware continuity);
  - `ingest` — `IngestionEngine`: `full_import` + `incremental_update`, window
    computed from store coverage, quarantine on bad data, idempotent.
- **universe/** (extends Phase 1) — `CategoryFilter` added; `UniverseManager`
  (symbol master via the `instruments` table, point-in-time metrics frame,
  eligibility); `build_filters(config)` for min volume / price band / min traded
  value / min market cap / sector / black-white-list — all configurable.
- **scanner/** (extends Phase 1) — `ScanEngine`: processes every eligible stock,
  runs every enabled strategy, emits ranked `Opportunity` objects, records every
  firing candidate to evidence. Pluggable `prepare_fn` (indicators, Phase 3) and
  `score_fn` (confidence, Phase 4) so later phases don't touch it.
- **schedule/** — `DataJobs`: `full_import`, `daily_update`, `intraday_refresh`
  — callable, idempotent batch jobs (no daemon; a live loop is paper-phase).

## Reuse (Phase 2 built on Phase 1, no redesign)

Filters/orchestrator (`universe.base`/`universe.py`), the `Scanner` ABC +
`Opportunity` + `rank_opportunities`, `StrategyProfile`, `EvidenceLogger`,
`TradingCalendar`, `MarketConfig`, and the config pattern were all reused
directly. New dependency: `pyarrow` (parquet engine), added to `pyproject.toml`.

## Validation performed

- `pytest`: **64 passed** (32 Phase 1 + 32 Phase 2). New Phase-2 coverage:
  store round-trip/dedup, provider determinism/session-awareness/CSV, quality
  gates, ingestion + incremental + idempotency + quarantine, universe filtering
  correctness + drop attribution, scanner (processes every stock, ranking,
  evidence recording, performance), scheduling jobs.
- End-to-end on-disk integration smoke: 25 symbols → full import (2,750 rows) →
  idempotent re-run (all `up_to_date`, zero duplicate downloads) → universe
  eligibility → scan of every eligible stock (14 ranked candidates, ~60 ms).

## Phase 1 (committed `4f4d630`)

Package foundation under `src/algo`: core primitives, SQLite evidence DB +
logger, strategy plugin interface + registry, universe-filter framework, scanner
interface, research-engine skeleton, and the relocated validation package. Crypto
code preserved under `archive/crypto-freqtrade/`.

## Environment

- Python 3.11 venv, editable install. pandas 3.0.3 / numpy 2.4.6 / pyarrow 25.
- Market data store: parquet under `user_data/data/` (gitignored). Evidence DB
  under `user_data/evidence/` (gitignored). Freqtrade/Docker not required.

## Next (Phase 3 — pending owner approval)

1. **Wire a live data source** — implement `NseProvider` (or a broker provider)
   once the owner picks the source and supplies credentials; the pipeline is
   source-agnostic, so nothing else changes. Until then, `CsvDataProvider`
   imports real exports.
2. **Research-engine port** — promote the edge lab from the archive, the outcome
   labeler, and the trade simulator; equity-parameterize the validation package;
   add the indicator-enrichment `prepare_fn` for the scanner.
3. **First candidate strategies** — measured, not deployed (the D-007 gate).

## Open decisions needed from owner

- Approve committing Phase 2.
- Data source + broker account (Zerodha / Dhan / Upstox / vendor / EOD).
- Authorize drafting `architecture/VALIDATION_RULES_EQ.md` (equity thresholds).

## Open blockers

- None. Phase 2 is self-contained and validated on synthetic + CSV data; a live
  feed is a provider implementation away.
