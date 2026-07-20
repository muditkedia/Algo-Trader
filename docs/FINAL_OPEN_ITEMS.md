# FINAL OPEN ITEMS

_Independent release audit, 2026-07-19. Everything that is NOT closed by code,
stated plainly. No engineering defects remain; every item below is either a
live-API validation gap, an operational control, or an accepted design
limitation._

## 1. Assumptions that require validation against the LIVE SmartAPI

The adapter is written to the documented SmartAPI contract and unit-tested
against a faithful fake client, but **it has never spoken to the real order
API**. Each assumption below is isolated, cheap to verify, and should be
confirmed during the supervised smoke test (§2).

| # | Assumption | Where | If wrong |
|---|---|---|---|
| A1 | `placeOrder` returns `{status, data:{orderid}}` (bare order-id string also handled) | `place` | Order id not captured → handled: treated as REJECTED, raises |
| A2 | `ordertag` is accepted on placement **and echoed back in `orderBook`** | `place`, `order_status`, `open_orders` | Idempotent reconciliation falls back to `orderid` only; a lost-response order could not be matched by tag |
| A3 | Order-book field names: `orderid`, `status`, `filledshares`, `averageprice`, `quantity`, `tradingsymbol`, `transactiontype`, `ordertype`, `ordertag` | `order_status`, `open_orders` | Fills/statuses misread → reconciliation wrong |
| A4 | Status vocabulary matches `_STATUS_MAP` (`complete`, `open`, `pending`, `trigger pending`, `rejected`, `cancelled`, `open pending`, `modified`, `partially filled`) | `_STATUS_MAP` | Unknown status → order keeps prior state and stays visible (safe default), but reconciliation is imprecise |
| A5 | `position()` returns `netqty` and `netprice`/`avgnetprice`, sign-positive for long | `positions` | Position sync wrong → recovery mis-adopts |
| A6 | `cancelOrder(orderid, variety)` positional signature; `variety="NORMAL"` | `cancel` | Cancel fails → now raises (does not silently mark cancelled) |
| A7 | `producttype="INTRADAY"` is the correct MIS code for equity intraday | `place`, `modify` | Order rejected by RMS → REJECTED path, event-logged |
| A8 | `ltpData(exchange, tradingsymbol, symboltoken)` signature | `quote` | Marking falls back to `None` (never crashes) |
| A9 | Real order rate limit is respected by `broker_min_interval_s = 1.0` | `_throttle` | Rate-limit responses → backoff/retry path |
| A10 | Rejection/expiry message wording matches the classifiers (`_is_rate_limited`, `_is_auth_expired`, `_is_network`) | `_call` | Mis-classified error → falls through to a plain `BrokerError` (safe), but retry/refresh may not trigger |

**Known imprecision (accepted):** `_is_auth_expired` matches the substring
`token`, so an error mentioning `symboltoken` would trigger one unnecessary
session refresh before failing. Harmless (bounded, then raises); noted so it is
not mistaken for a bug later.

## 2. The one mandatory pre-go-live step

**Supervised single-order live smoke test.** In a live session, with the
smallest viable stake and a single liquid symbol:

1. Arm live (three keys), start with `--once`.
2. Let exactly one entry be placed. Verify against the broker terminal:
   order id, `ordertag` present in the order book, quantity, product = MIS,
   status transitions, and the fill price.
3. Confirm the dashboard, `portfolio.json`, and the event log agree with the
   broker.
4. Restart the process while the position is open; confirm recovery **resumes**
   it (not orphan-closes it) and that no duplicate order is placed.
5. Trigger an exit (or square off) and confirm the position closes cleanly on
   both sides.
6. Tick off A1–A10 above from what you observed.

Only after this passes should the full watchlist be enabled.

## 3. Operational controls that exist only in procedure (no code enforcement)

| Item | Risk | Why not code | Control |
|---|---|---|---|
| **Two instances running simultaneously** | Critical — both would manage the same positions | Would require a lock-file/PID design; adding it now is a new feature outside the audit mandate | Runbook §7 forbids it |
| **Unattended failure (no alerting/paging)** | High — a dead loop goes unnoticed | External integration, deliberately out of scope | Runbook requires supervision; health beat + dashboard each tick |
| **Corporate-action screening** | High — candles are unadjusted; a split looks like a crash | An adjustment layer is a data-platform project, explicitly out of scope | Runbook §10: exclude affected symbols that day |
| **Config typos silently defaulting** | High — wrong stake could be used | Schema validation would be a new feature; the effective values are already logged | Runbook §2 step 3: verify the preflight `risk_config` line |

## 4. Accepted design limitations (not defects)

- **Software-managed stops.** Exits are market orders fired by the TradeManager
  on the bar that breaches the level — backtest-faithful. There is no resting
  stop-loss order at the broker, so a gap beyond the stop between bars is
  exited at the next observed price. The adapter supports `STOPLOSS_MARKET`
  if resting stops are wanted later.
- **Bar-close granularity.** Signals and exits are evaluated on completed bars
  (15m/1h), not ticks — identical to the validated backtests.
- **Modelled paper fills.** Slippage + the NSE cost stack; no queue position or
  market impact.
- **Long-only, single-position-per-symbol, fixed stake.** By design.
- **Legacy `algo/paper` and `algo/scanner` packages** remain in the repository,
  unused by the production stack (retained per the project's no-delete rule).

## 5. Items explicitly closed by this audit

- ✅ RB-1 SmartAPI response-envelope validation (5 proven failure modes) —
  fixed, 9 regression tests.
- ✅ RB-2 whole-share position sizing (paper/live divergence + false partial
  fills on restart) — fixed, 3 regression tests.
- ✅ Configuration fail-safety, secret handling, dangerous-option interlocks —
  audited, no defects.
- ✅ Startup / shutdown / recovery paths — audited, no defects.

## 6. Statement

**This project is engineering-complete and the remaining validation must occur
against the real broker and live market.**
