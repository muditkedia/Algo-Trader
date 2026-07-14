# Project State

_Last updated: 2026-07-14_

## Current Phase

**Phase 3 - Validation Tooling + Phase A Smoke Test (done, NOT committed).**
The validation protocol is frozen (`1b9a139`); the companion tooling it
requires (VALIDATION_RULES SS24) is BUILT and self-tested
(`user_data/scripts/validation/` - 12 modules + `run_validation.py` + README;
self-test 84/84 PASS), and the **Pipeline Smoke Test (SS18) has PASSED**
end-to-end on a minimal BTC/USDT window.

Phase A results (2024-03-01->2024-06-30, BTC/USDT only):
- Gate 0 CLEARED: lookahead-analysis "no bias detected" (20 signals);
  recursive-analysis indicator drift 0.000% at startup 199 vs 3600.
- Smoke backtest ran (25 trades); coordinator metrics match Freqtrade
  exactly (net -6.577 USDT, win 32%, DD 0.85%, mean hold 218 min).
- 18-section report generated; verdict FAIL - EXPECTED (uncalibrated
  defaults, 4-month window; SS18 says smoke is plumbing-only, not a
  strategy judgment).

No full historical dataset downloaded. No trading logic modified. No
parameters tuned. Full validation NOT started - awaits approval.

Implementation fix during Phase A: added `user_data/config_analysis.json`
overlay (entry/exit `price_side: "other"`) required because
lookahead/recursive-analysis force market orders internally; applied only to
those two analysis commands, not to trading or backtesting. Also extended
`run_validation.py` with optional `--daily-dir/--pairs` to load 1d OHLCV for
the regime breakdown. No strategy/algo_core changes.

## Environment

- Freqtrade **2026.6** (`freqtradeorg/freqtrade:stable`), CCXT 4.5.61
- Docker Desktop 4.82.0, Compose v5.3.0
- Exchange: **Binance Spot**, pairs BTC/USDT + ETH/USDT (StaticPairList)
- Mode: **DRY-RUN only** (`dry_run: true`). Live trading NOT enabled.
- GitHub remote renamed to `Algo-Trader` (case); local origin still
  old-cased, works via redirect. Update when convenient.

## Architecture (see architecture/ARCHITECTURE.md)

- `AdaptiveTrendStrategy` (thin orchestrator) -> `algo_core/`:
  settings.py (single source of truth for every threshold, configurable via
  `config.algo_trader`), indicators.py, regime.py (RegimeDetector: enum
  TREND/RANGE/HIGH_VOLATILITY/LOW_VOLATILITY/UNKNOWN, classifies TREND for
  now), decision_engine.py (mandatory gates + advisory-only conviction
  score, JSONL rejection audit), risk_engine.py (ATR/structure stop, 6%
  hard cap, opt-in risk sizing), trade_manager.py (monotonic stop ratchet,
  objective exits), profiles/ (trend_following ACTIVE + 4 scaffolds).
- Offline validation harness: `user_data/scripts/validate_strategy.py`
  (49+ checks, all passing at last run).

## Validation Protocol (frozen 2026-07-14)

`architecture/VALIDATION_RULES.md` - 25 sections. Key decisions:

- Binance spot; design pairs BTC/ETH; **8 unseen pairs** (SOL, BNB, XRP,
  ADA, DOGE, LINK, AVAX, LTC) for cross-pair generalization.
- Timeframes 1m/5m/15m/1h/4h/1d; corpus 2020-01 -> 2026-06 (+1-month
  pre-roll for the 3600-candle warmup).
- Partitioning: dev corpus 2020-01->2025-06; walk-forward IS 12mo / OOS 3mo
  / step 3mo (~18 folds); locked holdout 2025-07->2026-06 (one look);
  cross-pair OOS on the holdout window.
- **Risk-adjusted acceptance** (no CAGR gate): PF/Sharpe/Sortino/Recovery/
  Expectancy/MaxDD with Monte-Carlo CI bounds, regime stability, >=6/8
  unseen pairs, holding-time IQR 30-120 min.
- Gate 0: lookahead-analysis (0 findings) + recursive-analysis (stable at
  startup 3600) before any performance is trusted.
- **Pipeline Smoke Test first**: BTC-only small sample proves download ->
  integrity -> bias gates -> backtest -> report before the full download.
- Regime reporting (Bull/Bear/Range/High-Vol/Low-Vol via objective daily
  EMA50/ADX + trailing ATR% percentiles); holding-time distribution
  (median/IQR gates, bimodality flag); sensitivity at +/-5/10/20% per
  threshold (plateau vs cliff); Monte-Carlo CIs (10k resamples) gating on
  lower/upper bounds; 5 explicit stress tests; **portfolio-level battery**
  (worst-case simultaneous-stop <=15%, reject >20%); Mode B
  retraining-cadence study (Monthly/Quarterly/Semi-annual, quarterly prior).
- Standardized 18-section Markdown report per backtest.
- Companion tooling required (8 scripts, NOT yet built - separate approved
  tasks): regime bucketing, sensitivity sweep, Monte-Carlo CI, stress
  harness, report generator, portfolio analysis, cadence harness,
  RegimeDetector metrics.

## Validation Tooling (built, uncommitted)

- Coordinator pipeline: load -> metrics -> holding-time -> portfolio ->
  Monte Carlo -> stress -> regime (when daily OHLCV supplied) -> 18-section
  report; structured JSONL logs per step in user_data/logs/.
- Deliberate placeholders (per protocol phase): stress.delayed_exits needs
  candle data (interface frozen); walkforward Mode B Optimizer raises
  NotImplementedError (separate approved task); loader JSON fallback
  verified on a synthetic fixture - real export format exercised at the
  smoke test; regime step activates when daily OHLCV exists.
- Sensitivity grid: 228 one-at-a-time points over every configurable
  threshold at x{0.80,0.90,0.95,1.05,1.10,1.20}; plans backtest commands,
  executes nothing.
- Walk-forward: monthly=54 / quarterly=18 / semiannual=9 folds over the dev
  corpus verified; Mode A planning + aggregation + WFE + SS10.1 cadence
  parsimony scoring implemented.

## Completed Work

- Phase 1: verified Freqtrade dry-run environment (`c4024cd`).
- Phase 2: adaptive strategy framework, audited + revised (`f974226`).
- Phase 3a: validation protocol frozen (`1b9a139`).
- Phase 3b: validation tooling built + self-tested (uncommitted).

## Open Blockers

- None.

## Next Recommended Task (pending approval)

1. Commit the validation tooling.
2. Execute Phase A (Pipeline Smoke Test) per VALIDATION_RULES SS22 - the
   report generator it needs now exists.
3. Full download + Gate 0 + Mode A walk-forward.
