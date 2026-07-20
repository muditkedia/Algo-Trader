# PAPER TRADING GUIDE

_Deployment phase, 2026-07-19. Paper mode is fully functional and runs the
IDENTICAL pipeline to live; only the execution adapter (PaperBroker) differs.
See `docs/PRODUCTION_STACK.md` for the component architecture and
`docs/DEPLOYMENT_CHECKLIST.md` for the full audit._

## 1. What paper mode is

`mode: "paper"` selects **PaperBroker** — a deterministic, slippage-aware fill
simulator — as the only mode-dependent component. Market data, scanner,
strategy evaluation, risk engine, portfolio engine, order manager, trade
manager, logging, dashboard, recovery and the scheduler are the same objects
live mode uses. Nothing can place a real order in paper mode.

## 2. Run it

```
# one scheduled tick (management + any due timeframes), then a dashboard:
.venv/Scripts/python scripts/run_trading.py --mode paper --once

# continuous loop (sleeps to the next bar close; square-off/kill stay responsive):
.venv/Scripts/python scripts/run_trading.py --mode paper --loop

# snapshot only:
.venv/Scripts/python scripts/run_trading.py --mode paper --dashboard
```

With SmartAPI credentials in `.env`, the feed tops up the store in real time
(`IngestionEngine`, throttled + quality-gated). **Without credentials paper
still runs** — the feed serves whatever the store already holds (offline), so
the pipeline is fully exercisable with no broker at all.

## 3. Every paper-mode requirement — where it lives, how it's verified

| Requirement | Component | Verified by |
|---|---|---|
| Real-time market data | `feed.refresh` → `IngestionEngine.incremental_update` | reuses the data layer's own tests; offline fallback covered by integration tests |
| Scanner scheduling | `Scheduler.due` (bar-close cadence + grace) | `test_scheduler_*` |
| Signal generation | `Orchestrator.evaluate` (frozen strategies, dedup per bar) | `test_full_cycle_opens_a_position_from_a_real_orb_signal` |
| Risk checks | `AccountRiskEngine.check_entry/check_day` | `test_risk_blocks_every_documented_path`, `test_daily_loss_limit_trips_and_latches` |
| Position sizing | `AccountRiskEngine.position_size` (fixed stake / entry) | risk tests + integration |
| Order creation | `OrderManager.market_entry/market_exit` (idempotency keys) | `test_order_*` |
| Simulated fills | `PaperBroker.place` (slippage on the hurting side) | `test_paper_fills_with_slippage_and_accounts_positions` |
| Partial exits | `TradeManager` partial → breakeven → T2 → `_execute_partial` | `test_manager_target_then_partial_...`, `test_partial_on_the_last_manageable_bar...` |
| Trailing stops | `TradeManager` chandelier + `column` | `test_manager_chandelier_trail_ratchets`, `test_manager_column_trail_follows_the_line` |
| Position lifecycle | `PortfolioEngine` (OPEN→CLOSING→CLOSED) | portfolio + integration tests |
| Daily P&L | `PortfolioEngine.daily_stats` / `monitoring.daily_summary` | portfolio tests |
| Auto square-off | engine `_squareoff_all` at cutoff/emergency | `test_manager_squareoff_when_past_cutoff` + engine gating |
| Logging | `EventLog` (JSONL/day) + structured logs | present in every path; recovery/summary read it |
| Recovery after restart | `RecoveryManager` (broker-first) | `test_recovery_*`, `test_restart_recovery_resumes_the_open_position` |

## 4. Fill semantics (why paper tracks the backtest)

The TradeManager — shared with live — decides WHEN and at what honest price a
position exits (stop-first, gap fills at the open, same-bar pessimism), exactly
as the verified backtest engine does. PaperBroker only turns that decision into
a fill at the marked price ± `paper_slippage_pct`. So a paper run reproduces
backtest behaviour, and the code path is byte-identical to live.

## 5. Configuration (paper-relevant fields)

`mode="paper"`, `symbols_file`, `store_dir`, `state_dir`, `timeframes`,
`history_bars` (keep ≥1600 so path-dependent indicators like Supertrend are
correct), `squareoff_*`/`entry_cutoff_*`, `paper_slippage_pct`, and the full
`risk` block. Pass a JSON file via `--config`. Defaults are production-safe.

## 6. State & restart

Portfolio state persists atomically to `state_dir/portfolio.json` after every
mutation; event logs are `state_dir/events-YYYYMMDD.jsonl`; daily summaries
`state_dir/summary-YYYY-MM-DD.json`. On restart the engine recovers before
trading. **PaperBroker's book is per-process**, so recovery finds no broker
position and deterministically closes the persisted one at last price (logged)
— the safe mirror of live resuming from the real broker. To force a flat halt
at any time, create the kill-switch file (`state_dir/KILL`).

## 7. Limits (honest)

Paper P&L uses modelled slippage and the same NSE cost stack the backtests use;
it does not model queue position, real partial-fill microstructure, or impact.
It is a faithful pipeline and fill-convention simulator, not a market simulator.
