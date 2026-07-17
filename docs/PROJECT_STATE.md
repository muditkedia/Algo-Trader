# Project State

_Last updated: 2026-07-17_

## Current Phase

**Phase 8 (research pipeline) + Phase 8.5 (research planning layer) COMPLETE,
uncommitted — awaiting owner approval.** Phases 1–7 committed (`4f4d630`,
`5a14fd6`, `aa212c1`, `7a1aeac`, `40392cf`, `c3e38c4`, `e898842`).

## Phase 8.5 delivered — research planning layer (D-028, D-029)

- **`research/IMPLEMENTATION_ROADMAP.md`**: every candidate scored on twelve
  evidence dimensions and tiered (IMPLEMENT FIRST: B1 time-series momentum,
  A3 52-week high, D2 earnings-gap continuation · SOON: B3, H1, C2, D3, B7,
  then the cross-sectional seam, then A1, E1 · LATER: 10 · NOT FEASIBLE: 8 with
  named unblockers). Diversified ten-strategy queue with effort / research
  value / qualitative survival probability / failure risks / dependencies per
  strategy. Survival ceiling deliberately "moderate" — calibrated to the 0-for-6
  base rate and the corrected D-028 gate.
- **Part C practitioner audit** (library §3): added **B7 Elder triple screen**
  and **H1 Wyckoff spring** (objective components isolated; non-codable
  narrative parts explicitly excluded); expanded **B3** with the mechanical
  Turtle/Darvas rules (pyramiding documented as NOT integrable); mapped
  CAN-SLIM / institutional ORB / RS-leaders onto existing entries so they are
  never double-counted. Library now 29 candidates.
- **D-029 — RELIANCE/TCS root cause found and fixed**: a 1d-only pre-flight
  smoke test seeded the two symbols from 2025-01-01, and
  `incremental_update` only ever extended coverage FORWARD, so the full
  2023 download reported them `up_to_date` while silently missing 2023–24
  (evidence: mtime forensics — the pair was written 14s before the alphabetical
  batch and never touched again; 1h/15m were full because the smoke test was
  1d-only). Fixed with head-gap backfill (loud `backfilled` status; no-start
  callers like the paper engine unchanged); both symbols repaired live to the
  full 877 bars; store audit clean (only JIOFIN late — genuine listing date).
- **D-028 — stationary block-bootstrap CIs for long horizons**, reusing
  `monte_carlo.py`'s existing implementation. Verified on the real corpus: all
  six verdicts identical, 15m CI-lows bit-identical, **ema200_daily CI-low
  23.4 → 6.2 bps / nr7_daily 4.0 → −13.0 bps** — the day bootstrap had been
  overstating long-horizon confidence ~17 bps. Promotion rules untouched.
  Synthetic-overlap widening + planted-PASS controls added.
- Validation: **240 tests pass** (9 new: 4 backfill, 5 bootstrap). Real-sweep
  verification 303s. Next: implement B1 (time-series momentum) ALONE.

## Phase 8 delivered — the research pipeline (D-027)

The bottleneck is no longer infrastructure; it is finding a strategy with an edge
that clears costs. Phase 8 made adding a candidate cheap **without changing a
single verdict**.

- **One seam**: `ResearchEngine.research` / `research_all` runs the whole loop
  (register → record signals w/ confidence → label outcomes → measure edge →
  simulate → evaluate → cost sensitivity → calibration → verdict). It was
  hand-wired in `run_measurement.py`; a second entry point would have duplicated
  it. `_judge` is the single implementation of the evaluation.
- **Strategies are discovered, not listed**: `algo.strategies.library` discovers
  its own modules via the existing `StrategyRegistry`. **Adding a candidate is
  one file** — the hand-maintained `ALL_STRATEGIES` tuple is gone. Name
  uniqueness is enforced at import instead of by a test.
- **Pre-registered horizons**: `meta.horizon_bars` / `meta.max_hold_bars`,
  defaulting to exactly the Phase-5/7 values (1/2/4/8, 8-bar hold). This closes a
  real gap — the horizon was hardcoded platform-wide, so a candidate needing
  weeks could only be measured over 8 bars. **No CLI flag exposes it**: choosing
  a horizon after seeing a verdict is the tuning D-026 forbids.
- **Throughput, measured not assumed**: the suspected bottleneck (indicators
  recomputed 3×) was **0.4%** of the run. The real cost was `record_signals` at
  **54%** — a transaction (disk sync) + a SELECT per signal. Reusing the logger's
  existing batch writer cut it **14.77s → 0.60s (24×)**, the pipeline **2×**, and
  a re-run to **0.04s**. (D-025's labeling fix, applied to the recording step it
  had left per-row.)
- **`--strategies` subset filter**: iterating on one candidate no longer
  re-measures the five on record.
- **`research/reporting.py`**: league table + verdict detail promoted out of the
  script, so every entry point renders identical evidence.

### Phase 8 validation

- **231 tests pass** (25 new; baseline 206 unchanged).
- **The decisive proof — D-026 reproduces exactly.** Re-running the full real
  sweep (99 symbols, 2.85M bars, 6 strategies) through the new pipeline against a
  copy of the production evidence DB returns **every verdict and every headline
  number identical** to the recorded D-026 table (signal counts to the unit; PF,
  edge, CI-low, cost to the recorded precision), in 192s, with
  `recorded=0 labeled=0` — proving idempotency on real data.

## Phase 8 research report — `research/CANDIDATE_LIBRARY.md`

27 candidates across momentum, trend, volatility compression, events, low-risk,
mean reversion and seasonality, each with hypothesis, horizon, weaknesses,
cost sensitivity and references (including deliberate counter-references).

**The organizing finding:** cost is fixed per round trip (~31 bps delivery, ~12
intraday) while edge scales with the move, so the D-007 hurdle as a *share of the
target move* is the survival metric. The library is therefore biased to
multi-week/multi-month holds — which is D-007's own Option 2 and L-006's own
conclusion, now backed by NSE evidence.

**Recommended first implementation: time-series (absolute) momentum** — most
replicated effect in the literature, implementable on the current interface with
zero platform change, nearly parameter-free (so a FAIL indicts the market, not
the encoding), and aimed at the one real opening in the evidence: at the longest
horizon ever measured here (8 bars), ema200_daily's edge came within 4% of the
bar.

### Open decisions this raised (flagged, NOT taken)

1. ~~Block-bootstrap CIs before judging any long-horizon candidate~~ —
   **RESOLVED in Phase 8.5 (D-028)**, verdict-preserving, verified on the real
   corpus.
2. **A cross-sectional seam.** `entry_signal(dataframe) -> Series` is per-symbol,
   so cross-sectional momentum / relative strength / low-vol ranking — the
   best-documented, most cost-survivable families — **cannot be expressed**. The
   fix is additive (an optional `prepare_cross_section` the engine calls once per
   sweep), not a redesign. Out of scope for Phase 8.
3. **Multiple testing.** Evaluating ~25 candidates at a 95% bound yields ~1.25
   expected false PASSes from noise alone, and `EdgeReport.best()` already picks
   the best of several horizons without adjusting for that choice. A
   high-throughput pipeline without this control is a false-discovery factory
   (Harvey/Liu/Zhu 2016).
4. **Data gaps that block whole families**: no sector map (`instruments.sector`
   **0/99**), no earnings calendar (blocks PEAD — the highest-value acquisition
   available), no index-membership history, long-only (blocks pairs trading).
5. ~~DATA DEFECT: RELIANCE and TCS truncated~~ — **RESOLVED in Phase 8.5
   (D-029)**: root cause was the forward-only incremental update after a
   1d-only smoke-test seed; pipeline fixed with head-gap backfill; both symbols
   repaired to full 877-bar coverage; store audit clean.
6. **`ARCHITECTURE.md` is stale** — it still documents the retired Freqtrade
   crypto system (Binance spot, 5m/15m/1h/4h, `user_data/strategies/algo_core/`),
   which D-009 retired. `SYSTEM_OVERVIEW.md` is **empty**. The Indian-equity
   architecture is documented only across DECISIONS/PROJECT_STATE.

### Known limitation, deliberately not fixed

`label_outcomes` and `simulate_strategy` each run `simulate_trade` over every
signal — the same simulation computed twice, ~85% of the remaining runtime. They
are not trivially unifiable (the labeler uses raw bars + a fixed ATR(14); the
simulator uses the prepared frame and honours a strategy-supplied `atr`), and
resolving it changes measurement, which this phase froze. Worth its own phase.

## SECURITY EVENT (2026-07-17) — contained, no leak

Live SmartAPI credentials were placed in the **tracked** `.env.example`, and a
second copy existed in an un-ignored `smartapi.env`. Verified: **the secrets
were never committed to any branch** (`git log -S` across `--all` returns
nothing; `HEAD`'s template was still empty). Remediation: secrets moved to the
git-ignored `.env`, the tracked template restored to empty placeholders, and
`.gitignore` widened to `*.env` / `.env.*` (with `!.env.example`) so no `.env`
variant can ever be staged. `smartapi.env` is now ignored but is a redundant
plaintext copy — **recommend deleting it** (`.env` holds everything).
No rotation is required, since nothing was published.

## Phase 7 delivered — Part A: production execution rules

- **A1 max 3 positions** (`PortfolioConfig.max_open_positions = 3`): a fourth
  opportunity is decided against the book — OPEN / SKIP / REDUCE / REPLACE —
  with the reasoning persisted to evidence.
- **A2 one capital pool** (`max_capital_deployed = 1.0`): dynamically allocated
  across the three slots; capital idles only when a configured risk limit binds.
- **A3 buying power, never hardcoded** (`paper/buying_power.py`): cash |
  multiplier | broker modes; `SmartApiSession.rms_limits()` reads the real
  `rmsLimit` funds; `max_multiplier` guard against a mis-parsed field. Leverage
  raises the notional ceiling; **risk stays equity-based** (D-024).
- **A4 net break-even** (`risk/breakeven.py`): stop moves to entry grossed up by
  brokerage + STT + exchange + GST + stamp + SEBI + slippage + buffer (from the
  configured CostModel), and only once a configurable trigger is met (default
  3× round-trip cost) — never immediately after entry (D-023).
- **A5 trailing modes** (`risk/trailing.py`): promoted ATR chandelier stays the
  default; percentage / structure / volatility added behind one interface;
  profit-lock ladder applies in every mode; strategies opt in via declarative
  `meta.trail_mode` — **no strategy code changed**.
- Removed a duplicate `daily_risk_budget` field (PortfolioConfig owns it).

Part A validation: **200 tests pass** (21 new).

## Phase 7 — Part B: real market (COMPLETE)

- **SmartAPI login: VERIFIED** against the live API (session → profile
  `MUDIT KEDIA`, exchanges `nse_cm`/`bse_cm` → refresh → logout).
- **Universe**: `scripts/build_universe.py` generates `nifty100.txt` from the
  live instrument master (2,406 NSE `-EQ`) → **99/101 active**. `TATAMOTORS`
  (demerged; successor `TMPV`) and `LTIM` are absent — genuine index drift, not
  substituted (that would be inventing index membership).
- **Historical download: 99/99 symbols on all three timeframes**,
  **2,846,753 real NSE bars**, 2023-01-01 → 2026-07-17
  (1d 85,651 · 1h 604,058 · 15m 2,157,044).

### Two real defects the live run exposed (both fixed, both tested)

1. **Rate limiting mistaken for bad data.** 47 of 75 quarantines were
   `Access denied because of exceeding access rate` — the API enforces harder
   than its documented 3/s. The SDK surfaces it as a JSON *parse* error, so it
   looked like corruption. Fix: detect it, exponential backoff + retry,
   conservative 1.0s default throttle. Recovered 1h 74→99 and 15m 55→99.
2. **One bad bar discarded a whole symbol.** SmartAPI returns rare impossible
   bars (BRITANNIA **1 bad in 21,829** = 0.005%; CANBK 4 in 21,828). The
   all-or-nothing gate threw away 3.5 clean years over one tick. Fix: an
   explicit, counted, logged row-level drop with a
   `max(max_invalid_rows, pct × rows)` tolerance — the absolute floor matters
   because 1 bad bar in an 877-row *daily* series is 0.11% while the identical
   defect in 15m data is 0.005%. Systematically broken feeds are still
   quarantined whole. Recovered 1d 93→99.
3. **Labeling was O(signals × bars)** (a linear frame scan per signal ≈ 1.3
   billion comparisons) plus one disk sync per row. Fix: a date→row index built
   once per symbol + batched writes. ~12 outcomes/s → 62,166 in minutes.

### REAL measurement result: ALL SIX STRATEGIES FAIL

99 symbols · **125,794 signals · 125,719 labeled outcomes** · no tuning.

| strategy | verdict | signals | PF | expectancy | edge bps | CI-low bps | cost bps |
|---|---|---|---|---|---|---|---|
| ema200_daily | **FAIL** | 1,402 | 1.20 | +0.00248 | 59.4 | 23.4 | 30.9 |
| nr7_daily | **FAIL** | 898 | 1.03 | +0.00036 | 50.1 | 4.0 | 30.9 |
| volexp_1h | **FAIL** | 11,086 | 0.69 | −0.00121 | 9.1 | 2.4 | 12.2 |
| orb_15m | **FAIL** | 23,706 | 0.66 | −0.00123 | 3.0 | 0.3 | 12.2 |
| vwap_15m | **FAIL** | 26,517 | 0.58 | −0.00124 | 2.8 | 0.1 | 12.2 |
| pullback_15m | **FAIL** | 62,185 | 0.52 | −0.00149 | 0.2 | −2.0 | 12.2 |

All strategies are `rejected` in the evidence DB; **the paper engine refuses to
start (exit 3)** — the gate works.

**What the evidence says (not opinion):**
- **The intraday strategies are killed by costs, exactly as crypto was**
  (D-007/L-006 reproduced on Indian equities): 0.2–9.1 bps of gross edge
  against a 12.2 bps round trip. MFE/|MAE| 1.03–1.16 — barely better than a
  coin flip. Sharpe −4 to −11.7. These are not close.
- **The daily strategies have REAL edge but it does not clear delivery costs
  with confidence**: ema200_daily's 59.4 bps point edge exceeds the 30.9 bps
  cost and its expectancy is *positive* (PF 1.20), but the 95% CI lower bound
  (23.4 bps) sits below cost, and PF misses the 1.25 floor. It is the only
  candidate worth further *research* — not deployment.
- **Confidence carries no signal**: correlation with realized P&L is −0.026 to
  +0.018 across all six. The Phase-4 heuristic scores are, on this evidence,
  worthless — precisely the L-003 finding, now reproduced on NSE. They must be
  replaced by evidence-calibrated priors, not hand-tuned.

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
