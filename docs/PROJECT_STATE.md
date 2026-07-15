# Project State

_Last updated: 2026-07-15_

## Current Phase

**Equities pivot — Phase 1 (platform foundation) COMPLETE, uncommitted.**

The project has pivoted from the crypto/Freqtrade system to a research-first
quantitative platform for **Indian equities** (owner-approved 2026-07-15). Phase
1 built the reusable, market-agnostic foundation only. There are deliberately
**no strategies, brokers, market data, paper trading, or live trading yet** —
those are later phases. Everything below is in the working tree, awaiting owner
approval to commit.

## What Phase 1 delivered (`src/algo/`, editable-installed package)

- **core** — `config` (frozen-dataclass pattern + `MarketConfig` with NSE
  defaults), `logging`, `enums` (shared vocabularies), `calendar`
  (`TradingCalendar` interface + `StaticCalendar`), `costs` (`CostModel`
  interface + `FlatCostModel`). All market-agnostic; no NSE rules hardcoded.
- **evidence** — the SQLite evidence database: `schema` (10 tables), `database`
  (`EvidenceDB` lifecycle + versioning), `models` (typed rows), `logger`
  (`EvidenceLogger` — records **every** signal, outcome, trade, evaluation,
  regime label, and run lineage). This is the upgrade of the crypto
  `RejectionRecorder` into a full evidence store.
- **strategies** — `StrategyProfile` plugin interface (entry signal + declarative
  metadata; no per-strategy exits) and a multi-active `StrategyRegistry` with
  package discovery.
- **universe** — `Filter` framework (`ColumnRangeFilter`, `MembershipFilter`) +
  `Universe` orchestrator with per-symbol drop attribution. Framework only.
- **scanner** — `Scanner` interface + `Opportunity` + pure `rank_opportunities`.
  No broker, no data, no loop.
- **research** — `ResearchEngine` skeleton (evidence → validation battery →
  `evaluations`; league table) with explicit Phase-2 `NotImplementedError`
  placeholders for market-data-dependent edge measurement/labeling; and the
  **relocated validation package** (metrics, Monte Carlo, walk-forward,
  sensitivity, stress, portfolio, regime, report, coordinator).

## Reused / adapted (maximum reuse mandate)

- The **validation package** moved `user_data/scripts/validation/` →
  `src/algo/research/validation/`. Internal imports made relative;
  `sensitivity.build_grid` decoupled from `algo_core` (now takes the params to
  perturb); self-test decoupled from `user_data`. It passes its full self-test
  unchanged in substance under the new Freqtrade-free venv (pandas 3.0/numpy 2.4).
- The equity annualization change (252-day, session-aware returns) is
  **deliberately deferred to Phase 2** — doing it before a calendar exists would
  make it inconsistent with the daily-resample. Documented in `metrics.py`.

## Archived (preserved, not deleted) — `archive/crypto-freqtrade/`

Freqtrade strategies (v1/v2/v3), `algo_core`, the Phase D/E/F research scripts,
`config.json`/`config_analysis.json`, `docker-compose.yml`, the hyperopt/notebook
samples. The archive README flags the market-agnostic reuse candidates
(`indicators`, `risk_engine`, `trade_manager`, `decision_engine`) for
**promotion in Phase 2**, adapted not rewritten.

## Validation performed

- `pytest`: **32 passed** (core, evidence + FK enforcement, strategies + plugin
  loading, universe, scanner, research engine, and the validation self-test).
- Validation package self-test: **all checks PASS** as an installed module.
- Import graph: every `algo.*` module imports cleanly.
- On-disk startup smoke: DB file creation, schema install, end-to-end
  registry→logger→research wiring, and persisted-DB reopen all green.

## Environment

- **Python 3.11** virtualenv (`.venv`), package editable-installed via
  `pyproject.toml` (src layout). pandas 3.0.3 / numpy 2.4.6.
- **Freqtrade and Docker are no longer required** by the platform.
- Local runtime data lives under `user_data/` (gitignored): market data store,
  `user_data/evidence/` for the DB, logs. Crypto feathers retained locally, unused.

## Crypto phase (now archived history)

v1/v2/v3 all FAILED Mode A. The decisive finding (L-006/L-009, D-007): the entry
edge was ~3 bps gross/trade vs a ~20 bps round-trip fee — under-powered, not
mis-calibrated. Strategy iteration was stopped. Those lessons are the reason the
new platform is measurement-first. Full detail preserved in `docs/LEARNINGS.md`,
`docs/DECISIONS.md`, and `architecture/VALIDATION_RULES.md`.

## Next (Phase 2 — pending owner approval)

1. **Market foundations**: concrete NSE `TradingCalendar` (holiday list), the
   Indian equity `CostModel` (brokerage + STT + exchange + GST + stamp + SEBI,
   intraday vs delivery), instruments master.
2. **Data layer**: EOD ingest + corporate-action adjustment + quality gates +
   the static daily universe.
3. **Research engine port**: promote the edge lab from the archive
   (`measure_entry_edge`/`entry_edge_lab`), the outcome labeler, the trade
   simulator (promoting `trade_manager`/`risk_engine`), and equity-parameterize
   the validation package.

## Open decisions needed from owner

- Approve committing Phase 1 as it stands.
- Phase 2 kickoff; intraday data source / broker account (Zerodha/Dhan/Upstox).
- Holding-horizon scope (recommend: let the research engine measure MIS + CNC).
- Authorize drafting `architecture/VALIDATION_RULES_EQ.md` (equity thresholds).

## Open blockers

- None. Phase 1 is self-contained and validated.
