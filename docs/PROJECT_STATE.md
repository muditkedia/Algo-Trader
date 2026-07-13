# Project State

_Last updated: 2026-07-14_

## Current Phase

**Phase 2 - Strategy Framework (revised post-audit, NOT committed).**
The adaptive trading architecture is built, independently audited, revised
against that audit, and re-validated offline. No historical data downloaded,
no backtests, no hyperopt - deferred by instruction. Awaiting approval to
commit.

## Environment

- Freqtrade **2026.6** (`freqtradeorg/freqtrade:stable`), CCXT 4.5.61
- Docker Desktop 4.82.0, Compose v5.3.0
- Exchange: **Binance Spot**, pairs BTC/USDT + ETH/USDT (StaticPairList)
- Mode: **DRY-RUN only** (`dry_run: true`). Live trading NOT enabled.
- GitHub remote renamed to `Algo-Trader` (case); local origin still old-cased,
  works via redirect. Update when convenient.

## Architecture (see architecture/ARCHITECTURE.md)

- `AdaptiveTrendStrategy` (thin orchestrator) -> `algo_core/`:
  - `settings.py` - **single source of truth**; `AlgoSettings.from_config`
    builds frozen IndicatorParams / EngineParams / RiskParams. Every threshold
    configurable via `config.algo_trader`; unknown keys ignored.
  - `indicators.py` - canonical indicator set for 5m/15m/1h/4h
  - `regime.py` - `RegimeDetector` (classifies TREND for now; extension point
    for RANGE/VOLATILE without changing the strategy interface)
  - `decision_engine.py` - GO/NO-GO split into **mandatory gates** (trend,
    market_structure, liquidity, risk) and **advisory** (RSI, volume,
    volatility). Only advisory feeds the conviction score (`min_score` 0.60).
    Rejections recorded to `user_data/logs/trade_rejections.jsonl`.
  - `risk_engine.py` - ATR/structure stop, 6% hard cap, profit-lock tiers,
    risk sizing (opt-in via `enable_risk_sizing`, clamps to max not proposed)
  - `trade_manager.py` - monotonic stop ratchet + objective exits (floors from
    RiskParams); NO time exits
  - `profiles/` - StrategyProfile interface + registry; uniform
    `(settings, trade_manager)` constructor; `supported_regimes`;
    trend_following ACTIVE, four inactive scaffolds

## Post-audit revisions applied (all 7)

1. `startup_candle_count` derived from indicator periods -> **3600** (warms 4h EMA50).
2. Risk sizing corrected (clamps to max_stake) and gated by `enable_risk_sizing`
   (default off -> honest fixed-stake behaviour; no inert code).
3. Duplicated thresholds eliminated - profile reads settings; verified no
   profile-local threshold constants remain.
4. Every threshold configurable via `config.algo_trader` (IndicatorParams /
   EngineParams / RiskParams), demonstrated in config.json.
5. GO/NO-GO redesigned: mandatory hard gates vs advisory-only conviction score.
6. RegimeDetector introduced, wired into `confirm_trade_entry` routing.
7. Documentation updated (ARCHITECTURE.md, this file).

## Validation Completed

- `list-strategies`: AdaptiveTrendStrategy **OK** (config incl. `algo_trader`
  block validates cleanly).
- `validate_strategy.py`: **49/49 checks PASS** - resolver load + safety attrs,
  startup>=2400, MTF pipeline, 8 mandatory + 3 advisory checks, advisory-only
  conviction NO-GO, mandatory-failure NO-GO, hard-cap + spread NO-GO, sizing
  clamps to max, stop monotonicity, registry rules, single-source (no leaked
  constants + config override + gate follows settings), regime = TREND.

## Open Blockers

- None.

## Remaining Placeholders

- Four inactive strategy profiles (explicit scaffolds).
- `RegimeDetector._classify` returns TREND only (RANGE/VOLATILE deferred).
- All thresholds are initial values - uncalibrated until the first backtest.
- Empty docs: architecture/{SYSTEM_OVERVIEW,ROADMAP,CODING_STANDARDS,
  VALIDATION_RULES}, docs/{DECISIONS,LEARNINGS}, prompts/*.

## Next Recommended Task

1. Commit the revised framework (pending approval).
2. First backtest vertical slice: download bounded historical data, backtest
   trend_following, run lookahead-analysis / recursive-analysis bias checks,
   calibrate thresholds, document in LEARNINGS.md. Consider `stoploss_on_exchange`
   + protections before any live step.
