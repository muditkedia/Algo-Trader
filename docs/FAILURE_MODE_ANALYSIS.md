# FAILURE MODE ANALYSIS

_Independent release audit, 2026-07-19. For every component: "what would make
this fail in production?" — with probability, impact, and the mitigation that
exists in code today. Probability assumes a normal NSE trading day with the
system supervised per the runbook._

Legend — **Probability:** High (most sessions) · Medium (monthly) · Low
(rare/annual) · Very Low. **Impact:** Critical (capital loss or unmanaged
live risk) · High (missed/incorrect trading) · Medium (degraded operation) ·
Low (cosmetic).

## Broker / execution

| # | Failure | Prob | Impact | Mitigation (in code) |
|---|---|---|---|---|
| B1 | Order rejected by RMS (margin, freeze band, illiquid) | **High** | High | `_checked` marks it REJECTED (never OPEN), raises with the broker's message, event-logged; OrderManager records terminal state; error streak feeds the circuit breaker. **Fixed in this audit (RB-1).** |
| B2 | Session/JWT expiry mid-session | **High** | Medium | `_call` refreshes once and retries; `keepalive` refreshes on a cadence (`session_refresh_seconds`) |
| B3 | Rate limiting on order/read calls | Medium | Medium | `_call` throttles to `broker_min_interval_s` and backs off exponentially, bounded by `broker_max_retries` |
| B4 | Network interruption / API timeout | Medium | High | `_call` reconnects once and retries; persistent failure → `BrokerError` → error streak → circuit breaker halts entries; positions still managed |
| B5 | Ambiguous placement (request sent, response lost) | Low | **Critical** | Deterministic `client_order_id` + SmartAPI `ordertag`; OrderManager reconciles a known id via `order_status` instead of resubmitting; recovery never re-sends |
| B6 | Failed cancel leaves a live order | Low | **Critical** | Cancel is only recorded when the broker confirms; otherwise the order stays visible to reconciliation. **Fixed in this audit (RB-1).** |
| B7 | Broker read fails and looks "flat" | Low | **Critical** | `orderBook`/`position` raise on `status: False`; recovery logs the error rather than abandoning risk. **Fixed in this audit (RB-1).** |
| B8 | SmartAPI field/status vocabulary differs from assumption | **Medium** | High | `_STATUS_MAP` covers documented statuses; unknown status leaves the order's prior state and it remains visible. **Requires live validation — see FINAL_OPEN_ITEMS.** |
| B9 | Partial fill at the broker | Medium | High | Recovery sets open quantity to broker truth; `PARTIALLY_FILLED` retained as working in `open_orders` |
| B10 | Exchange freeze/circuit on a symbol | Low | Medium | Order rejected → B1 path; strategy simply gets no position |

## Market data

| # | Failure | Prob | Impact | Mitigation |
|---|---|---|---|---|
| D1 | Feed refresh fails (network/API) | Medium | High | Cycle catches it, marks `adapter_ok=False`, health degrades, error streak → circuit breaker; positions still managed on last known prices |
| D2 | Stale bars (provider lag) | Medium | High | Scheduler's grace window; health reports `data_fresh`; strategies only fire on the newest completed bar |
| D3 | Quarantined symbol (bad candles) | Medium | Low | Ingestion quarantines loudly and skips; other symbols unaffected |
| D4 | Missing history for a symbol | Medium | Low | Warmup gate (`min_history`) silently skips; preflight counts symbols with data |
| D5 | Corporate action (unadjusted candles) | Low | High | **No adjustment layer** — a split shows as a price jump and can trigger spurious signals. Mitigation is operational: the runbook requires checking the corporate-action calendar and excluding affected symbols that day |
| D6 | Special/short session (Muhurat) | Low | Medium | Clock trades only normal weekday sessions; special sessions are not auto-traded |

## Strategy / signal layer

| # | Failure | Prob | Impact | Mitigation |
|---|---|---|---|---|
| S1 | Strategy raises during `prepare`/`entry_signal` | Low | Medium | Orchestrator catches per strategy/symbol, logs, continues — one bad symbol cannot stop the scan |
| S2 | Path-dependent indicator (Supertrend) on a short window | Low | High | `history_bars` default 1600 feeds long history; documented as a must-keep-large setting |
| S3 | Signal storm (many symbols fire at once) | Medium | Medium | `max_open_positions`, exposure cap, and one-position-per-symbol gates bound it |
| S4 | Duplicate entry on the same bar | Low | High | Orchestrator dedups per (strategy, symbol, bar); risk engine blocks duplicate symbol/strategy |

## Risk / portfolio / state

| # | Failure | Prob | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Stop wider than the account tolerance | Medium | High | `max_loss_per_trade` gate rejects the entry before any order |
| R2 | Runaway losses | Low | **Critical** | `daily_loss_limit` latches the day off (realized + unrealized); positions still square off |
| R3 | Repeated broker/data errors | Medium | High | `circuit_breaker_errors` trips and halts new entries |
| R4 | Operator needs an instant stop | Low | Critical | Kill file (`state_dir/KILL`) → next tick squares off everything and halts |
| R5 | Crash mid-cycle | Low | High | State persisted atomically after every mutation; recovery reconciles on restart |
| R6 | Disk full / state dir unwritable | Very Low | **Critical** | Preflight probes writability and blocks startup; atomic writes prevent torn files |
| R7 | Clock/timezone drift on the host | Low | High | Clock is tz-aware IST; preflight logs `now=` and session status for operator verification |
| R8 | Config typo silently using defaults | Medium | High | Preflight logs effective risk values; runbook requires reading that line |

## Process / operations

| # | Failure | Prob | Impact | Mitigation |
|---|---|---|---|---|
| P1 | Live armed unintentionally | Low | **Critical** | Three independent keys; env var must be set in the shell; preflight refuses half-armed live; unarmed adapter refuses every order |
| P2 | Loop dies unnoticed | Medium | High | Health beat + dashboard each tick; **no external alerting** — the runbook requires supervision (see open items) |
| P3 | Restart after square-off time | Low | Medium | Recovery adopts/squares off anything open; entries gated by cutoff |
| P4 | Two instances running at once | Low | **Critical** | **No lock file** — mitigation is operational (runbook forbids it). Documented in FINAL_OPEN_ITEMS |

## Highest residual risks (ranked)

1. **B8 / live-API assumptions unvalidated** — the adapter has never spoken to
   the real order API (see `FINAL_OPEN_ITEMS.md`). Mitigation: the supervised
   single-order smoke test.
2. **P4 double-instance** — operationally prevented only.
3. **D5 corporate actions** — no adjustment layer; operationally screened.
4. **P2 unattended failure** — no paging/alerting; supervision required.
