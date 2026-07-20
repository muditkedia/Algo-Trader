# RELEASE READINESS REPORT

_Independent release audit, 2026-07-19. Mandate: assume nothing, attempt to
prove the system is NOT ready. Method: adversarial code audit of every
subsystem, configuration, dependency, startup/shutdown/recovery path, and the
AngelOne integration, with defects proven by executable probes before being
claimed._

## Verdict

**Two release-BLOCKING defects were found and fixed.** After the fixes the
suite is **541 passed, 1 skipped**. No further engineering defects remain.

The remaining risk is not code: it is that **the AngelOne live order API has
never been exercised against the real broker**. That is a validation gap that
can only be closed against the live API and live market.

## Release-blocking defects found (both fixed)

### RB-1 — CRITICAL: the live adapter trusted the SmartAPI response envelope

**Root cause.** SmartAPI reports *business* failures (RMS rejection,
insufficient funds, invalid token, market closed, order already executed) as
an HTTP 200 response carrying `status: False` and a `message`. It does **not**
raise. The adapter's retry wrapper only classified *exceptions*, so any such
response flowed through as if it had succeeded.

**Proven failure modes** (executable probes, all now regression-tested):

| # | Scenario | Behaviour before fix | Consequence |
|---|---|---|---|
| 1 | `placeOrder` → `{status: False, data: None}` | raw `AttributeError` | **crashes the trading cycle** — escapes `BrokerError` handling entirely |
| 2 | `placeOrder` → `{status: False, message: "RMS blocked"}` | order recorded **OPEN**, `broker_order_id=None` | system believes it holds a position it does not; the TradeManager would later "exit" a phantom position and book fictitious P&L |
| 3 | `cancelOrder` → `{status: False}` | order marked **CANCELLED** locally | order is still **live at the broker** and invisible to reconciliation — an unmanaged live order |
| 4 | `modifyOrder` → `{status: False}` | treated as success | stop believed moved when it was not |
| 5 | `orderBook`/`position` → `{status: False}` | read as **empty** | recovery sees "no positions / no orders" and abandons real live risk |

**Fix.** A single `_checked()` envelope validator now guards every
state-changing call and both reconciliation reads: `status: False` raises
`BrokerError` with the broker's message; a rejected order is marked
`REJECTED` (terminal), never `OPEN`; a success without an order id is
rejected; a failed cancel/modify leaves the order visible; failed reads raise
instead of looking flat. `modify`/`cancel` now also require an acknowledged
broker order id. Bare-string order-id responses (some SDK versions) still work.

**Files:** `src/algo/trading/adapters/angelone.py`.
**Tests:** `tests/test_release_audit.py` (9 tests, incl. 3 parametrized
rejection payloads).

### RB-2 — CRITICAL: fractional share quantities diverged paper from live

**Root cause.** `position_size` returned `stake / entry_price` — e.g.
50,000 / 2450.75 = **20.4019** shares. NSE cash equities trade in whole
shares. The live adapter sent `int(quantity)` = 20 while the portfolio
recorded 20.4019.

**Consequences.** (a) Paper and live executed *different quantities* from the
same signal — a direct violation of the project's core "one system" principle;
(b) every restart's reconciliation compared broker qty 20 against internal
20.4019 and **falsely reported a partial fill**; (c) P&L and exposure were
computed on a quantity that was never traded.

**Fix.** `position_size` floors to whole shares, and an entry whose stake buys
zero whole shares (share price above the stake) is rejected by the risk gate
with a clear reason. The backtest engine's documented fractional-share
convention is untouched — it is a measurement tool, not an order router.

**Files:** `src/algo/trading/risk.py`.
**Tests:** `tests/test_release_audit.py` (3 tests + the risk suite).

## Subsystem audit results

| Subsystem | Verdict | Notes |
|---|---|---|
| Data layer (store/ingest/quality/audit/manifest) | ✅ | Idempotent upserts, quarantine, atomic manifest; extensively exercised this project |
| Strategy library (12 intraday) | ✅ | Frozen; independently audited earlier (2 bugs fixed then) |
| Execution/backtest engine | ✅ | Frozen; adversarially audited earlier |
| Orchestrator / scanner | ✅ | Strategy-agnostic, per-bar dedup, newest-bar-only |
| Scheduler | ✅ | Bar-close cadence + grace (grace-window bug fixed in the prior phase) |
| Risk engine | ✅ | All gates tested; whole-share sizing fixed (RB-2) |
| Portfolio engine | ✅ | Atomic persistence (temp + `os.replace`), reload verified |
| Order manager | ✅ | Deterministic idempotency keys, bounded retry, reconcile-not-resubmit |
| PaperBroker | ✅ | Deterministic slippage fills; idempotent on client id |
| AngelOneBroker | ✅ *code* | Fixed under RB-1; **live API unexercised** (see FINAL_OPEN_ITEMS) |
| Trade manager | ✅ | Backtest-faithful conventions (stop-first, honest gaps, partial→BE→T2, trails) |
| Recovery | ✅ | Broker-first, deterministic, places no orders; hardened by RB-1 read fix |
| Preflight | ✅ | Blocks startup on any critical failure, incl. half-armed live |
| Logging / monitoring | ✅ | JSONL event log, daily summary, dashboard |
| Configuration | ✅ with caveat | See below |
| Dependencies | ✅ | pandas/numpy/pyarrow + optional `smartapi-python`/`pyotp` (lazy-imported; paper runs without them) |

## Configuration audit

- **Every parameter is documented** in `PRODUCTION_STACK.md` §3 and the two
  trading guides.
- **Invalid configurations fail safely:** an unknown/misspelled `mode`
  (`"Live"`, `"LIVE"`, `"papertrading"`) is rejected by `build_adapter` and
  never arms; incoherent risk limits (negative capital, zero stake) fail
  preflight and block startup.
- **Secrets are never logged:** SmartAPI credentials live only in
  `SmartApiConfig` with `repr=False` (verified: no secret appears in its
  `repr`); `TradingConfig` contains **no** secret fields at all.
- **Dangerous options cannot be enabled accidentally:** live trading needs
  three independent keys (config `mode`, config flag, and the
  `ALGO_ENABLE_LIVE=YES` environment variable), re-checked inside every
  order-placing call, plus a preflight refusal if live is only partly armed.
- **Caveat (documented, not a defect):** unknown/typo'd config keys are
  silently ignored and defaults apply. A typo'd `stake_per_trade` therefore
  yields the 50,000 default rather than an error. **Mitigation:** preflight
  logs the *effective* risk values at every startup — the runbook requires the
  operator to read that line before trading.

## Startup / shutdown / recovery paths

- **Startup:** `connect → recover → session rollover → preflight`; trading is
  refused unless every critical check passes. Verified in tests and by CLI run.
- **Shutdown:** CLI `finally` writes the daily summary and disconnects the
  adapter; state is atomic at every step, so any stop is safe.
- **Recovery:** broker-first reconciliation (resume / close orphaned-internal /
  adopt-and-flag orphaned-broker / detect partial fills), idempotent, never
  re-sends orders. Hardened by RB-1: a failed broker read now raises instead of
  masquerading as a flat account.

## Statement

Two genuine, release-blocking correctness defects were found by this audit and
have been fixed with regression tests. No further engineering defects remain.

**This project is engineering-complete and the remaining validation must occur
against the real broker and live market.**
