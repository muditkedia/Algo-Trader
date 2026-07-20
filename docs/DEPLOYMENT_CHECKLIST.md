# DEPLOYMENT CHECKLIST

_Deployment phase, 2026-07-19. Operator-facing go/no-go checklist for the
production trading system. Paper mode: ready now. Live mode: fully implemented,
disarmed, one supervised smoke test outstanding (see §D). Full suite: 528
passed, 1 skipped._

## A. Engineering completeness (all ✅)

- [x] One pipeline; paper/live differ only in the execution adapter — proven by
      `test_paper_and_live_open_identical_positions_only_adapter_differs`.
- [x] No duplicate paper/live logic; strategy-agnostic scanner/orchestrator.
- [x] 12 intraday strategies discovered from the registry; frozen, unmodified.
- [x] Execution specs, backtest engine, risk rules, trading rules — unchanged.
- [x] Idempotent orders (deterministic client-order-id + `ordertag`).
- [x] Bounded retries + circuit breaker + kill switch + daily-loss latch.
- [x] Broker-first deterministic recovery (resume / orphan-close /
      orphan-adopt / partial-fill detection).
- [x] Live `_call` robustness: throttle, rate-limit backoff, auth-refresh,
      network reconnect; `keepalive` session refresh.
- [x] Scheduler: bar-close cadence per timeframe + grace.
- [x] Atomic state persistence; JSONL event log; daily summary; dashboard.
- [x] Preflight gate blocks startup on any critical failure.
- [x] 528 tests pass.

## B. Pre-session operator checklist (run every trading day)

- [ ] `.env` present with valid SmartAPI credentials; `smartapi_login_check.py`
      passes.
- [ ] `state_dir/holidays.txt` current (today is a real session; not a holiday).
- [ ] Config reviewed: `risk` block sized for the day (stake, max positions,
      daily loss limit, max loss per trade).
- [ ] Watchlist file present; symbols have store data in the primary timeframe.
- [ ] Store is fresh (last bar recent); disk space for the day's logs/state.
- [ ] Kill file `state_dir/KILL` absent.
- [ ] Preflight PASSED (startup log shows every critical check OK).
- [ ] Recovery report reviewed (no unexpected orphaned-broker positions).
- [ ] Clock is IST-correct (preflight logs `now=` and session status).

## C. Startup / shutdown / restart

- [ ] **Startup:** `run_trading.py --config <cfg> --once` → confirm preflight
      PASSED and dashboard renders; then `--loop`.
- [ ] **Shutdown:** Ctrl-C → the loop persists state, writes the daily summary,
      disconnects the adapter cleanly.
- [ ] **Restart:** relaunch → portfolio reloads, recovery reconciles against
      the broker, management resumes from ratcheted stops; no duplicate orders.
- [ ] **Emergency:** create `state_dir/KILL` → next tick squares off all
      positions and halts entries.

## D. Live-enable gate (operator-only; keep OFF until every box is checked)

- [ ] Paper mode ran clean for a full session with expected behaviour.
- [ ] `mode="live"` and `live_trading_enabled=true` in the LIVE config only.
- [ ] `ALGO_ENABLE_LIVE=YES` exported in the trading shell ONLY (never in a
      persistent profile until intended).
- [ ] Conservative live `risk` block (minimum viable stake, low caps).
- [ ] **Supervised live smoke test:** one single-symbol minimum-quantity order
      in a live session; confirm order book, fill, position sync, and
      reconciliation all match; then flatten. (This is the one step not yet
      performed — the adapter has never touched the real order API.)
- [ ] Only after the smoke test passes: scale to the full watchlist.

## E. Known limitations carried into deployment (honest)

- Live order API not yet exercised against the broker (D above).
- Fills are modelled (slippage + NSE cost stack); no queue/impact simulation.
- Candle construction = provider completed bars (no bespoke tick aggregation).
- Broker-side resting stop-loss orders are supported by the adapter but the
  current TradeManager uses software-managed market exits (a deliberate,
  backtest-faithful choice; resting-stop routing is a future enhancement).
- The legacy `algo/paper` and `algo/scanner` packages are retained but unused
  by this stack (superseded; not deleted per project rule).

## F. Rollback

- Stop the loop (Ctrl-C or KILL file). State is atomic and self-consistent at
  every step, so a stop is always safe. Re-running in paper mode requires no
  cleanup. Disarming live is instant: unset `ALGO_ENABLE_LIVE` (orders refuse
  immediately) or switch `mode` back to `paper`.

**Go/no-go:** Paper mode — GO. Live mode — GO after the §D supervised smoke
test; disarmed until the operator completes it.
