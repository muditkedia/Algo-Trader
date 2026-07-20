# LIVE TRADING GUIDE

_Deployment phase, 2026-07-19. The AngelOne live adapter is **fully
implemented and disabled by configuration**. Live runs the IDENTICAL pipeline
to paper; only the execution adapter (AngelOneBroker) differs. **No live order
has been placed; the adapter has not been exercised against the real API** —
see §7._

## 1. The three-key safety interlock (live is off until ALL are set)

Live order placement requires, simultaneously:

1. `mode: "live"` in the config,
2. `live_trading_enabled: true` in the config, **and**
3. environment `ALGO_ENABLE_LIVE=YES`.

`AngelOneBroker` re-checks all three inside every order-placing method
(`place`, `modify`, `cancel`) and raises `BrokerError` otherwise; the
constructor logs a loud UNARMED warning when any is missing. Preflight fails
startup if `mode=live` but not fully armed (never half-armed). Read-only calls
(positions, orders, status, quote) are allowed unarmed so recovery and
preflight can reconcile against the account without the ability to trade.

**Verified:** `test_unarmed_live_still_refuses_orders`,
`test_live_needs_all_three_keys`, and a config-level check confirm an unarmed
live adapter refuses `place` and that arming needs all three keys.

## 2. Credentials (unchanged, from `.env` only)

`SMARTAPI_API_KEY`, `SMARTAPI_CLIENT_CODE`, `SMARTAPI_PIN`, and one of
`SMARTAPI_TOTP_SECRET` / `SMARTAPI_TOTP`. Secrets are masked and never logged.
Verify login independently first: `scripts/smartapi_login_check.py`.

## 3. Every live-mode requirement — where it lives, how it's verified

| Requirement | Component | Verified by |
|---|---|---|
| SmartAPI authentication | `AngelOneBroker.connect` → `SmartApiSession.login` (TOTP) | reused session tests; live login proven earlier this project (profile `MUDIT KEDIA`) |
| Session refresh | `AngelOneBroker.keepalive` (engine cadence) + auto-refresh on auth-expiry inside `_call` | `test_live_keepalive_refreshes_session`, `test_live_retries_and_refreshes_on_auth_expiry` |
| Instrument mapping | reused `SmartApiInstruments.token_for/tradingsymbol_for` | instrument tests |
| Order placement | `AngelOneBroker.place` (SmartAPI `placeOrder`, `ordertag` = idempotency key) | `test_live_place_succeeds_when_armed` |
| Order modification | `AngelOneBroker.modify` (`modifyOrder`) | routed through `_call`; stub-tested path |
| Order cancellation | `AngelOneBroker.cancel` (`cancelOrder`) | routed through `_call` |
| Position synchronization | `AngelOneBroker.positions` (`position`) | recovery tests via `ReconAdapter`/`FakeClient` |
| Order synchronization | `AngelOneBroker.open_orders`/`order_status` (`orderBook`) | recovery reconcile tests |
| Recovery after restart | `RecoveryManager` (broker-first) | `test_recovery_*` |
| Real-time broker reconciliation | recovery reads broker positions+orders, rebuilds internal state | `test_recovery_resumes_matching_position` etc. |
| API failure handling | `_call` wrapper classifies + handles rate-limit / auth / network | deployment tests |
| Retry policy | `_call` bounded retries (`broker_max_retries`) + `OrderManager` retry | `test_live_backs_off_on_rate_limit`, `test_order_manager_retries_...` |
| Rate-limit handling | `_call` throttle (`broker_min_interval_s`) + exponential backoff | `test_live_backs_off_on_rate_limit` |
| Network interruption recovery | `_call` reconnect-once on connection errors | `test_live_reconnects_on_network_error` |
| Duplicate-order prevention | deterministic `client_order_id` + SmartAPI `ordertag`; OrderManager reconciles a known id instead of resubmitting; recovery never re-sends | `test_order_manager_idempotent_reconcile_on_known_id` + recovery tests |

## 4. The robust call wrapper (`_call`)

Every SmartAPI SDK call routes through `AngelOneBroker._call`, mirroring the
verified historical-data provider's policy:

- **throttle** to `broker_min_interval_s` (order rate-limit compliance),
- **rate-limit** ("exceeding access rate") → exponential backoff, retry,
- **auth expiry** (token/jwt/unauthorized) → refresh the session ONCE, retry
  (falls back to a fresh login if refresh fails),
- **network** (timeout/connection/reset) → reconnect ONCE, retry,
- otherwise → wrapped `BrokerError`.

Bounded by `broker_max_retries`. `keepalive` refreshes the JWT on the
`session_refresh_seconds` cadence from the engine loop.

## 5. Going live (operator procedure — deliberately manual)

1. Confirm paper mode runs clean for the session (`--mode paper --loop`).
2. Independently verify login: `scripts/smartapi_login_check.py`.
3. Prepare `live.json` with `mode="live"`, `live_trading_enabled=true`, a
   conservative `risk` block (small `stake_per_trade`, low `max_open_positions`
   and `daily_loss_limit`), and your watchlist.
4. Export the third key in the shell: `export ALGO_ENABLE_LIVE=YES` (Windows:
   `$env:ALGO_ENABLE_LIVE="YES"`).
5. Start supervised: `scripts/run_trading.py --config live.json --once`, watch
   the event log and dashboard, confirm reconciliation and a single controlled
   order, then `--loop`.
6. To halt instantly: create the kill file (`state_dir/KILL`) → the next tick
   squares off everything and stops entries.

Do NOT set the env var in any persistent profile until you intend to trade
live; without it the system cannot place an order regardless of config.

## 6. Order model

Long-only intraday (MIS): market entries at the signal-bar close; exits are
market orders the TradeManager fires on stop/target/partial/trail/square-off
(same decisions as paper and backtest). Structural stops are enforced in
software by the TradeManager AND bounded by the account `max_loss_per_trade`
risk gate; a broker-side stop-loss order type is available in the adapter
(`STOP`/`STOPLOSS_MARKET`) for a future resting-stop enhancement.

## 7. Remaining pre-go-live step (honest)

The AngelOne adapter's SDK bindings follow the documented SmartAPI methods and
are unit-tested against a faithful fake client, but **have not run against the
live order API** (live disabled per instruction). Before real capital: run one
supervised, single-symbol, minimum-quantity live order in a controlled session
and confirm the order book, fill, position sync and reconciliation match — then
scale up. Everything else in the pipeline is exercised and green.
