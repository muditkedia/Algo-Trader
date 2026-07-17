# Project State

_Last updated: 2026-07-17_

## Current Phase

**Phase 6 (SmartAPI provider + paper engine + EXECUTION INTELLIGENCE) COMPLETE.**
Phases 1–5 committed & pushed (`4f4d630`, `5a14fd6`, `aa212c1`, `7a1aeac`,
`40392cf`).

**The project's production market-data source is Angel One SmartAPI** (D-020).
Kotak Neo is superseded (code retained, unused). CSV import remains a fallback,
not the primary path. Still **no live orders anywhere** — paper trading only.

## Execution-intelligence extension (D-022)

The scanner + paper engine were extended into the full production pipeline:

- **Canonical `Opportunity`** (scanner/base.py): prices + risk geometry from
  the promoted risk engine, expected R/R/return/holding, historical win rate /
  expectancy from evidence, confidence component breakdown, regime (daily
  labeler, cached), liquidity metrics, estimated NSE cost, reason, evidence
  reference — the standard object through scanner → ranking → execution.
- **Ranking engine** (scanner/ranking.py): configurable `RankingWeights` +
  `RankingScales` (nothing hardcoded); components = expected return, hist
  expectancy/win rate, confidence, calibration correlation, liquidity,
  reliability (sample size), cost penalty, risk/reward; missing evidence →
  neutral midpoint. **Every ranking decision persisted** (signal rank +
  `_ranking` breakdown; re-scans update rank, never duplicate).
- **Adaptive scan scheduler** (scanner/scheduler.py): per-timeframe cadence
  (15m≈45s, 1h≈3min, 1d≈7min — configurable); management never waits on scans.
- **Portfolio manager** (paper/portfolio.py): OPEN / SKIP / REDUCE / REPLACE
  against capital, deployed-exposure cap, max positions, sector caps
  (correlation proxy until return-correlation evidence exists), daily risk
  budget (soft/hard), replace-if-better margin. SKIPs recorded to evidence.
- **Dynamic sizing** (paper/sizing.py): risk-parity core (`risk_based_stake`
  reuse) × confidence multiplier, capped by risk budget, available capital,
  concentration and hard stake limits; below-minimum → 0 (honest skip).
- **Continuous loop** (paper/engine.py + scripts/run_paper.py): every tick
  manages positions (stops → trailing ratchet → 15:15 IST square-off); scans
  run only when due; JSON state resume; risk budget rolls at the IST day.
- **Console dashboard** (paper/dashboard.py): market status, strategies,
  universe/eligible, scan progress, open positions with unrealized PnL,
  capital deployed/available, realized PnL today, risk budget left, top
  ranked opportunities, health. Monitoring only.
- `evidence/queries.py`: shared read-side stats (one SQL home for calibration
  correlation + per-strategy overall stats — no duplication).

Extension validation: **179 tests pass** (19 new: scheduler cadence/force,
ranking weight-sensitivity/evidence-neutrality/bounds, every portfolio
decision path, every sizing cap, dashboard sections, market-status clock,
management-without-scan, portfolio-skip evidence, snapshot). End-to-end
offline demo: scan → 3 ranked candidates → 2 positions opened with sized
stakes → dashboard rendered → persisted ranking breakdown verified.

## Phase 6 delivered

- **SmartAPI provider** (`src/algo/data/providers/smartapi/`), built against
  the official SDK (`smartapi-python`/`SmartConnect`, verified from source):
  - `config` — credentials from `.env`/environment ONLY (stdlib loader in
    `core/config.load_env_file`), secrets masked, missing credentials reported
    by name with portal provenance;
  - `session` — login (`generateSession` + TOTP via pyotp), refresh
    (`generateToken`), `getProfile`, `terminateSession`; lazy SDK import,
    injectable client;
  - `instruments` — official scrip master JSON → NSE `-EQ` symbol/token map,
    parquet cache (works credential-free; **verified live: 2,408 NSE
    equities**);
  - `provider` — `getCandleData` chunked per documented per-interval day
    limits, throttled (~3 req/s), IST→UTC normalization, one refresh-retry on
    token expiry; `ltpData` latest quote. Supports 1m…1d incl. 15m/1h/1d.
- **Historical downloader** (`scripts/download_history.py`) — reuses
  IngestionEngine/store wholesale: incremental by coverage, resume = re-run,
  duplicate-proof, quality-gated, quarantine reporting.
- **`scripts/smartapi_login_check.py`** — 5-step verification; graceful named
  missing-credential report (exit 2) without credentials.
- **`scripts/run_measurement.py --store-dir`** — production measurement path:
  real store + real evidence DB; verdicts persist as lifecycle statuses
  (PASS→measured, FAIL→rejected; synthetic runs never advance a strategy).
- **Paper Trading Engine** (`src/algo/paper/engine.py` +
  `scripts/run_paper.py`) — bar-driven: manage (stop → ratchet → 15:15 IST
  square-off) then scan → select top-N → open; JSON state resume; every
  signal/trade recorded to evidence `mode=paper`; **gated: refuses strategies
  that have not survived measurement on real data** (`--allow-unmeasured` for
  offline validation only). NO live orders.
- `.env.example` rewritten for SmartAPI with exact portal provenance.
  `pyproject.toml` gains the optional `smartapi` extra.

## Validation performed (credential-free, per Task 4)

- `pytest`: **160 passed** (19 SmartAPI mocked: .env loading, auth flows +
  failure paths, instrument filtering + cache, candle parsing IST→UTC,
  chunking count, throttling, token-refresh retry, graceful unknowns,
  ingestion wiring + duplicate protection; 6 paper: gating refusal/override,
  open/stop-loss close, square-off, state resume, capacity + confidence floor).
- Live credential-free check: official instrument master downloaded (2,408
  NSE equities). Scripts exit gracefully with named missing variables.
- Offline end-to-end paper pipeline on synthetic store: signal → position →
  evidence → restart resume. Measurement regression green; statuses persisted.

## The moment credentials exist (no code changes needed)

1. `scripts/smartapi_login_check.py` — verify login.
2. `scripts/download_history.py --symbols-file <file>` — populate the store.
3. `scripts/run_measurement.py --store-dir user_data/data/nse` — real verdicts.
4. `scripts/run_paper.py --symbols-file <file> --loop` — paper trade the
   survivors (engine refuses if none survive — that is by design).

## Prior phases

P1 foundation · P2 data/scanning · P3 Kotak (superseded) · P4 six strategy
candidates · P5 measurement machinery (controls: planted edge PASSes, noise
FAILs; all six FAIL on synthetic — correct). See DECISIONS D-008…D-021.

## Open decisions / actions needed from owner

- Approve committing Phase 6.
- Create the local `.env` (values from smartapi.angelone.in; see .env.example).
- `pip install smartapi-python pyotp logzero websocket-client pycryptodome`
  (the `smartapi` extra) into `.venv` before first live use.
- Choose the measurement universe (symbols file, e.g. NIFTY-100 constituents).

## Open blockers

- Real verdicts + paper trading blocked only on the local `.env`.
