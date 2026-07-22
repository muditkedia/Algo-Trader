# PRODUCTION TRADING STACK

_Built 2026-07-19. One codebase for paper and live; the execution adapter is
the only interchangeable component. Live trading is fully implemented but
**disarmed by default** (three independent keys required). No strategy,
execution spec, or backtest-engine code was modified to build this._

## 1. Component architecture

Package `src/algo/trading/`. Each module has one responsibility; the pipeline
matches the approved diagram exactly.

```
MarketDataFeed ─▶ Orchestrator ─▶ AccountRiskEngine ─▶ PortfolioEngine
     (feed.py)     (orchestrator)      (risk.py)         (portfolio.py)
                        │                                      │
                        └─────────▶ OrderManager ─▶ ExecutionAdapter
                                    (ordermanager)   (adapters/)
                                                     ├── PaperBroker  (paper.py)
                                                     └── AngelOneBroker (angelone.py)
                                          │
        TradeManager (trademanager.py) ◀──┘   EventLog + Monitoring
        RecoveryManager (recovery.py)         (eventlog.py, monitoring.py)
        Preflight (preflight.py)              ProductionEngine (engine.py)
        MarketClock (clock.py)                Config (config.py)
```

| Component | File | Single responsibility |
|---|---|---|
| TradingConfig | `config.py` | all configuration; `mode` selects the adapter and nothing else; 3-key live arming |
| MarketClock | `clock.py` | market time truth: sessions, bar boundaries, square-off / entry-cutoff, holidays |
| MarketDataFeed | `feed.py` | keep the store current (reuses `IngestionEngine`) + serve warmed history frames |
| Orchestrator | `orchestrator.py` | run every enabled strategy over the watchlist, dedup per bar, emit normalized `TradingSignal`s — strategy-agnostic |
| TradingSignal / build_signal | `signals.py` | normalized signal; entry-time levels via the **verified engine's** `_initial_stop`/`_target` (reuse, no drift) |
| AccountRiskEngine | `risk.py` | pre-order account controls, daily loss limit, circuit breaker, emergency stop |
| PortfolioEngine | `portfolio.py` | live positions/orders/P&L/daily stats; atomic persistence |
| OrderManager | `ordermanager.py` | broker-independent order lifecycle: create/modify/cancel, idempotency keys, bounded retry |
| ExecutionAdapter | `adapters/base.py` | the one interchangeable contract |
| PaperBroker | `adapters/paper.py` | deterministic slippage-aware fill simulator |
| AngelOneBroker | `adapters/angelone.py` | live SmartAPI adapter (place/modify/cancel/status/positions), 3-key armed |
| TradeManager | `trademanager.py` | live interpretation of each position's ExecutionSpec — **same conventions as the backtest engine** (stop-first, honest gap fill, partial→BE→T2, chandelier/column trail, square-off) |
| RecoveryManager | `recovery.py` | broker-first, deterministic restart reconciliation |
| Preflight | `preflight.py` | startup validation gate; trading refuses to start on any critical failure |
| HealthMonitor / monitoring | `monitoring.py` | health beats, daily summaries, console dashboard |
| ProductionEngine | `engine.py` | wires the pipeline; runs preflight+recovery then cycles |
| CLI | `scripts/run_trading.py` | `--once` / `--loop` / `--dashboard`; paper default; never auto-arms live |

**Reused verbatim (not reimplemented):** `ExecutionSpec` + the engine's level
helpers, `RiskParams` + `trailing_stop_price`, `SmartApiSession` /
`SmartApiInstruments` / `SmartApiDataProvider`, `IngestionEngine` +
`MarketDataStore`, the strategy registry + all 14 registered strategies. This is
what guarantees live/paper fills track the backtest.

## 2. Test summary

**812 passed** overall after the STRAT-05 integration. Production-stack
coverage:

- `tests/test_trading_stack.py` (24): live-arming 3-key matrix; adapter
  selection; AngelOne unarmed-refusal + read-only reconciliation; every risk
  denial path; daily-loss latch; circuit breaker + kill switch; paper fills
  with slippage + idempotency + position accounting; order-manager retry →
  error-streak and idempotent reconcile; TradeManager (stop-first, honest
  gap, same-bar pessimism, partial→BE→T2, chandelier trail, column trail,
  square-off); atomic portfolio persistence/reload; recovery (resume,
  orphaned-internal, orphaned-broker adoption, partial-fill detection); clock
  bar boundaries.
- `tests/test_trading_integration.py` (4): a crafted feed drives a **real
  `orb_5m` signal** through the full engine → confirmed paper fill and
  position (collared limit entry, midpoint/ATR-capped stop); same-bar re-scan does not
  double-enter; honest gap-through stop books a loss; restart persistence +
  deterministic recovery; no-signal → no entry.

Every component is independently testable (no hidden globals; the adapter,
feed, and clock are injectable).

## 3. Configuration guide

`TradingConfig` (JSON, passed via `--config`; all fields optional):

| Field | Default | Meaning |
|---|---|---|
| `mode` | `"paper"` | `"paper"` or `"live"` — selects the adapter, nothing else |
| `live_trading_enabled` | `false` | second live key |
| `symbols_file` | `nifty100.txt` | watchlist (one symbol/line) |
| `store_dir` | `user_data/data/nse` | candle store (shared with the platform) |
| `state_dir` | `user_data/trading` | portfolio state, event logs, summaries, holidays.txt, KILL |
| `timeframes` | `[]` | optional restriction; empty derives 5m/15m/1h from enabled strategies |
| `history_bars` | `1600` | history handed to strategies (full-history for path-dependent indicators) |
| `squareoff_hour/minute` | `15:15` | intraday square-off (IST) |
| `entry_cutoff_hour/minute` | `15:00` | no new entries after (IST) |
| `kill_switch_file` | `user_data/trading/KILL` | presence ⇒ emergency stop |
| `paper_slippage_pct` | `0.0002` | PaperBroker per-side slippage |
| `risk.*` | see below | account risk limits |

`RiskLimits`: `max_capital` 500k, `stake_per_trade` 50k, `max_open_positions`
5, `max_exposure_pct` 1.0, `daily_loss_limit` 15k, `max_loss_per_trade` 2.5k
(the account guard for uncapped structural stops — verification blocker
closed), `allow_multiple_strategies_per_symbol` false,
`circuit_breaker_errors` 5.

Credentials: SmartAPI keys come from `.env` ONLY (unchanged; masked). The
holiday file is `state_dir/holidays.txt` (one ISO date per line, from the
exchange calendar).

## 4. Paper / Live switching guide

**Paper (default):** `mode="paper"`. Runs with or without SmartAPI creds
(offline serves the store). Nothing can place a real order.

**Live requires ALL THREE keys** (defence in depth — any one missing = no
live orders):
1. `mode: "live"` in the config, AND
2. `live_trading_enabled: true` in the config, AND
3. environment `ALGO_ENABLE_LIVE=YES`.

`AngelOneBroker` re-checks all three inside `place`/`modify`/`cancel` and
raises `BrokerError` otherwise; read-only reconciliation (positions, orders,
status, quote) is allowed unarmed so recovery/preflight can inspect the
account. Preflight additionally fails startup if `mode=live` but not fully
armed (never half-armed live). **No business logic branches on the adapter**
— the engine, orchestrator, risk, portfolio, order manager, and trade manager
are byte-identical between modes.

To go live (when approved): set the two config keys, export the env var, run
`scripts/run_trading.py --config live.json --loop`. Until then the AngelOne
adapter stays disabled.

## 5. Restart & recovery design (Phase 8)

Recovery reconciles against **broker truth first**, then rebuilds internal
state; it is deterministic, fully logged (RECOVERY events), idempotent, and
places **no orders**.

1. Reload persisted portfolio (positions, orders, realized P&L) — written
   atomically (temp + `os.replace`) after every mutation, so no torn state.
2. Read broker positions + open orders (the authority).
3. Reconcile positions:
   - tracked **and** at broker → **resume**, keeping the ratcheted
     stop/target/trail state (trailing state lives on the persisted Position).
   - tracked, **not** at broker → closed while down → **close in book** at
     last price (`orphaned_internal`), logged.
   - at broker, **not** tracked → **adopt** as `__orphan__`, flagged for
     immediate square-off (never left unmanaged), logged loudly.
4. Reconcile orders: terminal ones settled; working ones kept; status
   refreshed from the broker.
5. Detect partial fills (broker qty < expected) → set open quantity to broker
   truth.
6. Duplicate-placement prevention: recovery never re-sends; new orders use
   fresh deterministic idempotency keys, and OrderManager reconciles a known
   client-order-id instead of resubmitting.

Session rollover resets intraday realized P&L / closed-trade list on a new
day. **PaperBroker note:** its book is per-process, so on restart recovery
sees no paper broker position and safely closes the persisted one
(deterministic) — the mirror of live resuming from the real broker.

## 6. Production-readiness checklist (Phase 9 — preflight gates)

Trading refuses to start unless every **critical** check passes:

| Check | Critical | Verifies |
|---|---|---|
| broker_auth | ✔ | adapter `connect()` (live: login) |
| live_armed | ✔ (live only) | all 3 keys present or refuse |
| market_data | ✔ | ≥1 watchlist symbol has data in the primary timeframe |
| watchlist | ✔ | non-empty watchlist |
| strategies | ✔ | ≥1 enabled intraday strategy loaded |
| risk_config | ✔ | coherent limits (stake ≤ capital, positive guards) |
| storage | ✔ | state dir writable (probe write/delete) |
| portfolio | ✔ | portfolio initialized (positions loaded) |
| clock | ✔ | tz-aware IST clock |
| recovery | warn | reconciliation ran; orphan-broker adoptions surfaced |

Preflight result is logged and emitted to the event log at startup.

## 7. Known limitations / not in scope (honest)

- **No live orders were placed and the AngelOne adapter was not exercised
  against the real API** (per instruction — live disabled). Its SDK bindings
  follow the documented SmartAPI methods and are unit-tested against a stub;
  a supervised live smoke test remains a pre-go-live step.
- **Candle construction = provider completed bars** (no bespoke tick
  aggregation), by design — the same bars the backtests used.
- **Supertrend live windows:** the engine feeds `history_bars` (default 1600)
  of history, satisfying the path-dependence flagged in the verification
  audit; keep this large.
- The legacy `algo/paper` and `algo/scanner` packages are untouched and
  unused by this stack (superseded, retained per the no-delete rule).
