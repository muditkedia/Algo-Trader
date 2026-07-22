# END-TO-END VALIDATION

_Deployment phase, 2026-07-19. Confirms the full production pipeline runs as
one system, that paper and live differ ONLY in the execution adapter, and that
every component works together. Latest full suite: **791 passed**._

## 1. The central guarantee: paper and live are the same pipeline

`test_paper_and_live_open_identical_positions_only_adapter_differs`
(`tests/test_trading_deployment.py`) drives the SAME crafted `orb_5m` breakout
through TWO fully-wired `ProductionEngine` instances — one `mode="paper"`
(PaperBroker), one `mode="live"` armed against a fake SmartAPI SDK
(AngelOneBroker) — and asserts they produce the **same trading decision**:

- one position each, same symbol (`RELIANCE`), same strategy (`orb_5m`),
- same quantity and same stop (identical risk geometry),
- the live engine actually routed a real `placeOrder` through the SDK,
- the **only** difference is `adapter.name` (`paper` vs `angelone`).

Every stage between market data and the adapter — scanner, orchestrator, risk
engine, portfolio engine, order manager, trade manager, logging — is the same
object graph in both. There is no separate paper or live implementation.

## 2. Pipeline stages exercised together (integration, not units)

`tests/test_trading_integration.py` runs the whole engine on a crafted feed:

- **Market data → scanner → signal:** a real ORB breakout session flows
  through `feed → orchestrator.evaluate` and fires `orb_5m` on the newest bar.
- **Risk → sizing → order → fill → position:** the signal passes the account
  risk gate, is sized at the fixed stake, placed through the OrderManager,
  reconciled through PaperBroker and booked only after its limit-collar order
  is confirmed filled, with the STRAT-01 midpoint/ATR-capped stop.
- **Dedup:** a second scan on the same bar opens nothing (no duplicate).
- **Management → honest exit:** a next-session bar gapping through the stop
  exits at the open and books a loss (backtest-faithful gap fill).
- **No-signal path:** a quiet session opens nothing.
- **Restart:** persisted state reloads; recovery runs deterministically.

## 3. Component-by-component verification (Phase 4 audit)

| Component | Together-with-pipeline check | Status |
|---|---|---|
| Startup | `engine.startup()` runs recovery then preflight; refuses on any critical fail | ✅ |
| Shutdown | CLI `finally` writes daily summary + `adapter.disconnect()` | ✅ |
| Restart recovery | reload → broker reconcile → resume/close/adopt; idempotent | ✅ |
| Broker reconciliation | resume matched, close orphaned-internal, adopt orphaned-broker, detect partials | ✅ |
| Risk engine | every denial path + daily-loss latch + circuit breaker + kill switch | ✅ |
| Strategy loading | 12 intraday strategies discovered from the registry (no hand list) | ✅ |
| Scanner | strategy-agnostic evaluate over the watchlist, newest-bar-only, dedup | ✅ |
| Scheduler | bar-close cadence per timeframe + grace; quiet off-hours; sleep hint | ✅ |
| Logging | JSONL event log per day + structured logs on every path | ✅ |
| Monitoring | health beats, daily summary, console dashboard | ✅ |
| Configuration | single `TradingConfig`; `mode` selects adapter only; 3-key live arming | ✅ |
| Recovery | deterministic, broker-first, places no orders, fully logged | ✅ |
| Paper mode | full pipeline, offline-capable, backtest-faithful fills | ✅ |
| Live mode | full pipeline, disarmed by default, robust `_call` wrapper | ✅ (not run against real API — see LIVE_TRADING_GUIDE §7) |

## 4. Failure-path validation (adversarial)

- **Auth expiry mid-order** → refresh once, retry, succeed
  (`test_live_retries_and_refreshes_on_auth_expiry`).
- **Rate limit** → exponential backoff, retry, succeed
  (`test_live_backs_off_on_rate_limit`).
- **Network interruption** → reconnect once, retry, succeed
  (`test_live_reconnects_on_network_error`).
- **Persistent broker failure** → bounded retries, order marked REJECTED,
  error streak feeds the circuit breaker (`test_order_manager_retries_...`).
- **Duplicate placement** → known client-order-id reconciles instead of
  resubmitting (`test_order_manager_idempotent_reconcile_on_known_id`).
- **Ambiguous restart** → orphaned-internal closed, orphaned-broker adopted &
  flagged, partial fills detected (`test_recovery_*`).
- **Emergency stop** → kill file squares off + halts entries.

## 5. Integration test summary

| Suite | Tests | Scope |
|---|---|---|
| `test_trading_stack.py` | 24 | every stage as a unit (risk, orders, paper fills, trade manager, portfolio, recovery, clock, arming) |
| `test_trading_integration.py` | 4 | full engine on a crafted feed: open, stop-out, restart, no-signal |
| `test_trading_deployment.py` | 11 | scheduler cadence; live robustness (refresh/rate-limit/reconnect/keepalive); **paper==live equivalence** |
| **Production total** | **39** | — |
| **Whole project** | **528 passed, 1 skipped** | strategies, execution engine, data layer, research-freeze, adversarial, production |

## 6. Bugs fixed during deployment integration (objective only)

1. **Scheduler grace measured from bar OPEN, not CLOSE** — a just-opened 15m
   bar was "due" immediately instead of `grace` seconds after it completes.
   Fixed to `bar_close + grace`; `test_scheduler_respects_grace_window`.

No strategy, execution spec, backtest engine, risk rule, or trading rule was
modified. The only other changes were the additive robustness wrapper and the
scheduler (new components), both explicitly required by the phase.
