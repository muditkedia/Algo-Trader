# Project State

_Last updated: 2026-07-14_

## Current Phase

**Phase 1 - Environment Setup (in progress).**
A verified Freqtrade dry-run environment is standing. No strategy,
historical data, or backtest exists yet (intentionally deferred).

## Environment

- Freqtrade **2026.6** (Docker image `freqtradeorg/freqtrade:stable`)
- Docker Desktop 4.82.0, Compose v5.3.0, CCXT 4.5.61 (bundled)
- Exchange: **Binance Spot**
- Mode: **DRY-RUN only** (`dry_run: true`). Live trading is NOT enabled.

## Architecture / Key Decisions

- **Secrets externalized** via Freqtrade `FREQTRADE__` env-var overrides,
  documented in `.env.example`, injected through an optional `env_file`
  in `docker-compose.yml` (`required: false`, so a missing `.env` is
  non-fatal). `exchange.key`/`secret` stay blank in `config.json`; real
  keys are never committed.
- **`api_server` and `telegram` blocks omitted** while disabled: an empty
  `api_server.jwt_secret_key` fails the schema's `minLength: 32`. Re-enable
  later via `FREQTRADE__API_SERVER__*` / `FREQTRADE__TELEGRAM__*` env vars.
- **Pairlist:** `StaticPairList` with `BTC/USDT`, `ETH/USDT` (placeholder).
- **Windows note:** pass the config to the container as a *relative* path
  (`user_data/config.json`) to avoid Git Bash MSYS absolute-path mangling.

## Completed Work

- Committed documentation scaffold to Git (commit `01ab41a`).
- Initialized `user_data/` via the recommended command:
  `docker compose run --rm freqtrade create-userdir --userdir user_data`.
- Authored dry-run Binance Spot config `user_data/config.json`.
- Created `.env.example` (env-var template; secrets externalized).
- Wired optional `env_file` into `docker-compose.yml`.
- Updated this state document.

## Validation Completed

- `docker compose config --quiet` -> valid (exit 0).
- `show-config` -> config validated: `dry_run=true`, `trading_mode=spot`,
  `exchange=binance` (exit 0, no errors).
- `test-pairlist` -> Binance instantiated, `dry_run enabled`, whitelist
  resolved to `['BTC/USDT', 'ETH/USDT']` against live markets (exit 0).

## Files (uncommitted vertical-slice work, pending approval)

- `M docker-compose.yml`    - added optional `env_file` for `.env`
- `A .env.example`          - env-var template
- `A user_data/config.json` - dry-run Binance Spot config
- `A user_data/**`          - Freqtrade init output (dirs + sample files)
- `M docs/PROJECT_STATE.md`  - this file

## Open Blockers

- None.

## Open Decisions / Notes

- `create-userdir` copied Freqtrade **sample files** (`sample_strategy.py`,
  `sample_hyperopt_loss.py`, `strategy_analysis_example.ipynb`). Left in
  place pending a keep/remove decision. They are inert.
- Vertical-slice changes are **not yet committed** (awaiting approval).

## Next Recommended Task

1. Decide whether to keep or remove the Freqtrade sample files.
2. Commit the verified dry-run environment.
3. Begin the first strategy vertical slice:
   strategy -> download data -> backtest -> validation -> documentation.
