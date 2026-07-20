# Market Data Architecture v2

_Phase 7. A scheduler-driven market-data subsystem with a single source of
truth, replacing the one-request-per-symbol polling loop that could not scale
past ~250–400 symbols._

This document is the reference for the `algo.marketdata` package: what each
piece owns, how a request flows from "a bar is due" to "MarketState holds it",
how the system behaves under failure, and how it scales. It also records the
**SmartAPI batching audit** whose result shaped the whole design.

---

## 1. Why v2 exists

The Phase-6 benchmark found the production bottleneck was not CPU. It was the
market-data path itself:

- **one request per symbol**, issued every cycle whether or not a new bar had
  closed;
- **sequential timeframe fetching** with scheduling logic living inside the
  provider;
- **three independent pollers** — the cycle's `feed.refresh`, the live tier's
  private quote timer, and a separate bar scheduler — so the same symbol could
  be requested twice in a second while a due timeframe went unserved;
- a **REST architecture** whose cost was `symbols × cycles`, not
  `symbols × bars`.

v2 replaces this with a subsystem whose cost is one request per symbol **per
bar** (the irreducible minimum for a provider that cannot batch candles), that
never fetches a timeframe before it is due or skips it after, and that exposes
exactly one object — `MarketState` — to everything above it.

---

## 2. The layering

Each arrow is one-way. Nothing points back up; no lower layer imports a higher
one.

```
        enabled strategies  ─── derive ──▶  required timeframes
                                                  │
   ┌──────────────────────────────────────────────┴─────────────────┐
   │                     MarketDataService                           │
   │            the ONE owner — holds all five, fetches              │
   │                                                                 │
   │   TimeframeScheduler ──▶ RequestQueue ──▶ Transport ──▶ Source  │
   │      decides WHAT        orders/dedup      decides HOW    I/O    │
   │           │                                    │                │
   │           └──────────────▶ MarketState ◀───────┘                │
   │                       the single truth                          │
   └─────────────────────────────────────────────────────────────────┘
                                  ▲
              read-only, never fetch │
   ┌──────────────┬─────────────┬────┴─────┬─────────────┬───────────┐
 scanner      strategies      risk      execution     dashboard
```

| Module | Responsibility | Knows about |
|---|---|---|
| `capabilities.py` | What a provider can do, declared as data | nothing |
| `state.py` `MarketState` | The single runtime truth: candles, health, freshness, marks | store, freshness |
| `scheduler.py` `TimeframeScheduler` | Decides **what** is due; emits `DataRequest`s | MarketState (reads), clock, capabilities |
| `queue.py` `RequestQueue` | Orders by priority, de-duplicates | `DataRequest` only |
| `ratelimit.py` `AdaptiveRateLimiter` | A budget the scheduler plans against | `RateLimit` only |
| `transport.py` `Transport` | Decides **how**: executes one request, paces, gates quality | source, limiter |
| `source.py` `MarketDataSource` | Provider-specific I/O, behind an interface | a `DataProvider` |
| `service.py` `MarketDataService` | The one owner: `poll()` drives the whole pipeline one bounded step | all of the above |

The two halves of the seam — **scheduling** (`what`) and **transport**
(`how`) — never appear in the same function. That is what lets the transport be
swapped from REST to websocket to replay without the scheduler, scanner or any
strategy changing.

---

## 3. SmartAPI batching audit (principle 6)

**Verified against the installed SDK (`smartapi-python 1.5.5`), not assumed.**

| Question | Finding | Evidence |
|---|---|---|
| Multiple symbols per **candle** request? | **No.** One `symboltoken` per call. | `SmartConnect.getCandleData(historicDataParams)` takes a single dict with one `symboltoken`; no array form, no bulk historical route in `_routes`. |
| Multiple symbols per **quote** request? | **Yes — 50.** | `getMarketData(mode, exchangeTokens)` takes `{exchange: [token, …]}`; Angel One titles the endpoint "50-Symbol Bulk Fetch". Response carries an `unfetched` array for tokens past the cap. |
| Larger historical **windows**? | Yes, per-interval day caps (15m: 200 days, 1h: 400, 1d: 2000). | Used for backfill chunking; irrelevant to the live one-bar tail. |
| **Rate limits** | candles 3/s, 180/min, **5000/hour**; quotes taken conservatively at 1/s, 500/min, 5000/hour. | Angel One forum table (topic 4387); the quote announcement says 1/s and contradicts the table, so we take the lower. |
| Safe **concurrency** | 1 (strictly serial). | Limits are per-account; concurrency buys throughput only until the shared budget binds and makes rejection bursty. |

**Consequences the design is built around:**

1. **Candles cannot be batched on this provider.** The scheduler plans one
   request per symbol per bar; the rate limit is therefore the universe
   ceiling. This is a constraint to design *around*, not a gap to work around —
   pretending otherwise would silently drop symbols.
2. **Quotes can be batched 50:1.** Position marking for a 1000-symbol universe
   costs 20 requests, not 1000, on an independent budget from candles. v2
   implements this (`SmartApiDataProvider.fetch_quotes` → `getMarketData`);
   the old path used one `ltpData` call per symbol.
3. **The hourly cap is what binds at scale**, and it is invisible if you only
   model requests/second. One candle request per symbol per bar costs
   `symbols × bars_per_hour`:

   | universe | 15m (4 bars/h) | 5m (12 bars/h) | vs 5000/h budget |
   |---:|---:|---:|---|
   | 100 | 400/h | 1,200/h | fine |
   | 250 | 1,000/h | 3,000/h | fine |
   | 500 | 2,000/h | 6,000/h | 5m **over** |
   | 1000 | 4,000/h | 12,000/h | 5m **impossible**, 15m near ceiling |

   `MarketDataService.check_budget()` computes this at **startup** and logs an
   error if demand exceeds budget — the operator learns at 09:00, not at 12:30
   when the hour's budget runs out mid-session.

The audited numbers live in exactly one place: `SMARTAPI_CAPABILITIES` in
`capabilities.py`, with per-field provenance. A provider that gains a bulk
endpoint changes one declaration and the scheduler adapts.

---

## 4. The scheduler (principles 3, 4, 5)

`TimeframeScheduler` owns every timeframe and enforces three invariants, each a
real defect class before v2:

**Nothing is fetched early.** A timeframe is due only when the clock says a bar
has *completed* and the `grace_seconds` window (feed latency) has elapsed.
`expected_bar()` never returns the forming bar. Asking for a still-forming bar
returns a partial candle indistinguishable from a real one once stored.

**Nothing is skipped once due.** A due timeframe stays pending until every due
symbol has been *served* — meaning its stored data actually reached the
expected bar. A request that succeeded but left the symbol still behind (a
provider slow to publish, or an empty window) is **not** counted as served: it
is retried within the attempt budget, so a delayed bar is caught as soon as it
lands rather than being written off. A poll that runs out of budget defers
work; it does not drop it.

**Only due symbols are asked for.** `symbols_due()` returns only symbols whose
newest stored bar is *older* than the expected bar. A symbol already current is
not requested — this is the difference between a fetch that costs one request
per symbol per **bar** and one that costs a request per symbol per **cycle**.

### Incremental windows (principle 5)

`_candle_request` builds the window from **one bar after what is stored**, not
a fixed lookback:

```
stored tail ──▶ start = tail + one bar ──▶ end = expected bar close
```

So the REST request itself carries only the missing bars. A symbol the store
has never seen seeds a **bounded** window (`live_lookback_days`, default 5),
at backfill priority so seeding never delays a live bar — not the ingestion
default of a year, which would pull 365 days per symbol on the first refresh.

`tests/test_marketdata_scheduler.py::test_the_rest_request_window_is_incremental`
asserts this on the **source's recorded call**, not on the store — writing only
new rows to the store while downloading a year every time would look identical
from the store's side.

### The scheduler view an operator reads

```
timeframe   expected_bar   served_bar   due   pending   next_due
   5m         09:20          09:20       No      0       09:25:20
   15m        09:15          09:00       Yes    41       09:30:20
   1h          —              —          No      0       10:00:20
```

---

## 5. Request lifecycle

```
  clock says a bar closed + grace elapsed
              │
              ▼
  scheduler.plan(state)          ── reads MarketState, emits DataRequests
              │                     (sends NOTHING)
              ▼
  queue.extend(requests)         ── de-dupes, orders by priority band
              │                     (HELD ▶ DUE ▶ BACKFILL ▶ QUOTE)
              ▼
  service.poll():  while budget allows and queue non-empty:
     limiter.ready(now)?  ──no──▶ defer, return (caller keeps its loop)
              │yes
              ▼
     transport.execute(request)  ── paces, fetches, quality-gates
              │
              ▼
     service._apply(result):     ── the ONLY writer of market truth
        ok + rows   ▶ state.apply_candles() ▶ store + cache + health OK
        empty       ▶ record success, retry-if-behind (delayed bar)
        rate-limited▶ REQUEUE (bar still owed), widen limiter spacing
        unavailable ▶ health UNAVAILABLE, discharge (never re-queued)
        failed      ▶ health failure, retry within attempts
              │
              ▼
  scheduler.mark_served() ── discharges the bar only when data reached it
              │
              ▼
  service.completed_timeframes() ── bars whose pass finished, drained once
              │
              ▼
  engine scans exactly those timeframes, on data that ACTUALLY arrived
```

Because `poll()` is **bounded** (a request cap and a wall-clock budget) and
**never sleeps**, a 99-symbol pass that physically takes ~33 s at 3 req/s is
spread over many polls. The caller's loop keeps running square-off, the kill
switch and position management the whole time — a blocking drain would suspend
all three for half a minute.

---

## 6. MarketState — the single source of truth (principles 1, 12, 13)

Everything above the subsystem reads `MarketState` and **nothing else**. It
holds, and is therefore the only place that computes:

| Field | Meaning |
|---|---|
| `history(sym, tf)` | the warmed-up candle window, hot-cached over parquet |
| `marks(tf)` | `symbol → (close, bar_open_time)` — price and its bar travel together |
| `latest_prices(tf)` | last close per symbol (for marking) |
| `quotes` | live LTP for display/marking only — **no decision reads a quote** |
| `freshness(tf)` | candle-based lag vs the expected bar (delegates to the frozen module) |
| `symbol_health` | per-symbol success/failure; isolation lives here |
| `timeframe_health` | per-timeframe served/failed/pending |
| `last_update` | when candles last **arrived**, per timeframe |
| `latency_ms / p95` | rolling provider latency |
| `provider_status` | OK / DEGRADED / OFFLINE, from observed behaviour |
| `queue_state / rate_state` | pipeline depth and rate headroom (set by the service) |
| `diagnosis(tf)` | one operator verdict: "no new candles" vs "UNABLE TO FETCH N/M" |

**The cache is validated, not trusted.** `history()` keys its cache on the
parquet file's `(mtime_ns, size)`. A write by anything else — the history
downloader, a test, a second process — is picked up on the next read rather
than shadowed by a stale copy. That check is one `stat` (microseconds) against
a full parquet read (milliseconds), which is where the old
read-everything-every-cycle cost went.

**Freshness stays candle-based (D-039).** It is measured against the bar the
exchange should have completed, in bars, never against the wall clock and never
against an exporter's write time. `MarketState.freshness` delegates to
`algo.trading.freshness` — one definition, imported lazily so the market-data
layer never loads the trading package at import time.

**The dashboard computes nothing.** Every market-data figure on it —
freshness, symbol health, the fetch verdict, queue depth, rate headroom —
is read from `MarketState`/`MarketDataService`. There is one implementation of
each, so two panels cannot disagree.

---

## 7. Data-quality isolation (principle 10)

```
   99 configured
        │
        ├─ 96 served ──▶ usable, fresh, scanned
        └─  3 failed ──▶ health records the failure per symbol
                         │
              the pass still COMPLETES (pending → 0)
                         │
              freshness names the 3 that are behind
```

A symbol that fails to fetch but has stored bars stays **usable** — it is
tradeable on the bars it has, and one provider hiccup should not drop it. A
symbol that cannot be *addressed at all* (no instrument token) is set aside as
`UNAVAILABLE` and never even requested. Either way, three bad symbols out of
ninety-nine cost three: the scan proceeds on the ninety-six that are fine.
Nothing here can return an empty watchlist because of a provider fault.

---

## 8. Adaptive rate limiter (principle 7)

Replaces "sleep a fixed interval, back off on rejection", which had three
faults that only appeared under load: it could not see the hourly window, it
slept *inside* the transport (so priority was impossible), and its backoff was
amnesiac.

The replacement is a **passive budget**:

- **Multi-window.** Every declared `RateLimit` is enforced at once via sliding
  windows, so per-second, per-minute and per-hour all bind; the tightest wins.
- **Never sleeps.** `next_available(now)` answers *when*; the caller decides
  what to do with the interval. The whole thing is a pure function of recorded
  timestamps — deterministic, testable without a clock.
- **AIMD.** A rejection multiplies extra spacing; sustained success decays it.
  The limiter converges on the rate the provider *actually* enforces — the
  documented 3/s was observed rejecting at 2.5/s (D-020), so the true limit is
  not a number we can read anywhere.
- **`affordable(now, window)`** answers "can this universe be served at this
  cadence?" before the session, feeding `check_budget()`.

Sliding windows matter for the hour: a fixed bucket that resets on the hour
would let a caller spend 5,000 requests at 10:59 and 5,000 more at 11:01, which
the provider rejects even though a naive counter calls both legal.

---

## 9. Failure recovery

| Failure | Behaviour |
|---|---|
| **Provider offline** (none wired) | `NullSource` refuses every fetch *with a reason*; MarketState keeps serving stored candles; status OFFLINE, not "quiet market". Watchlist never emptied. |
| **Provider fails mid-session** | requests fail per symbol; after 3 consecutive blind polls, status OFFLINE; **recovers to OK on its own** when the provider returns and the next bar serves. |
| **Rate-limited** | request is **requeued** (bar still owed), never counted as a symbol failure; limiter widens spacing. The bar is served once the provider relents. |
| **Missing symbol** (no token) | UNAVAILABLE, never requested; reported distinctly from a quiet market (D-038). |
| **Stale data** | freshness reports STALE with the worst bars-behind; the health beat goes unhealthy; the scan still runs on what is fresh. |
| **Delayed bar** | not fabricated early; retried within the attempt budget so it is caught as it lands, not one interval late. |
| **Partial failure** | isolated per symbol; the pass completes; survivors scanned. |
| **Exchange closed** | nothing is due; not one request is issued off-session. |
| **Restart mid-fetch** | store writes are atomic per symbol/timeframe; a fresh service reads exactly what was committed — no partial bar. |
| **Restart during scan** | the orchestrator's in-memory dedup re-evaluates on restart, which is safe: only the newest bar can fire and it was already acted on; the duplicate-position guard stops a second entry. |
| **Dashboard / exporter throws** | the engine holds no dependency on observation; `_observe` swallows every exporter exception. A broken exporter cannot stop a cycle or square-off. |

Every row above is an executable test in
`tests/test_marketdata_adversarial.py`.

---

## 10. Provider abstraction & websocket readiness (principles 15, 16)

A live source implements `MarketDataSource`: `fetch_candles`, optional
`fetch_quotes`, `capabilities`, `unavailable_reason`, `is_rate_limited`,
`status`. `ProviderSource` adapts any existing `DataProvider` (synthetic, CSV,
Kotak, SmartAPI) so the whole provider ecosystem plugs in unchanged;
`for_provider()` attaches the verified capability profile by name.

Swapping SmartAPI REST for Zerodha, Polygon, a replay engine — or a **websocket
transport** — is implementing this one interface. No scanner, strategy, risk or
dashboard code moves.

**Websocket readiness (not built now, by design).** `capabilities.supports_
streaming` is the seam. A streaming source reports its own freshness and needs
no due-symbol computation; the scheduler consults `supports_streaming` rather
than assuming polling. When a websocket transport arrives, it fills
`MarketState` from the push side of the same seam the scheduler already writes
through — the scanner keeps reading `MarketState.history` and cannot tell the
difference. `tests/test_marketdata_service.py::test_swapping_the_source_
changes_nothing_a_consumer_sees` pins that invariant today.

---

## 11. Exactly one owner (principle 17)

Before v2 there were three pollers. Now there is one: `MarketDataService.poll()`,
called once per `engine.tick()`. There is **no thread, no timer, no background
task** anywhere in `algo.marketdata` — a second loop is a second owner. The
live tier's private quote timer and the separate bar scheduler are gone; the
runner script's `_live_wait` fast-tier loop is gone. One beat drives fetch,
manage, and scan.

---

## 12. Scaling behaviour

See `docs/PERFORMANCE_BENCHMARK.md` for the measured per-stage table. The shape:

- **CPU scales linearly** and is not the binding constraint — scanning 11
  strategies is ~330 ms/symbol, so 1000 symbols is ~40% of a 15m bar.
- **The binding constraint is candle request-throughput**, because candles
  cannot batch. At 3 req/s, N symbols cost ~N/3 seconds of wall time per bar:
  ~250 symbols is comfortable at 15m, ~400 is the practical ceiling, 500+ needs
  a longer bar or a second provider.
- **Quotes no longer scale with the universe** — batched 50:1, they are a
  rounding error.
- **The scheduler only fetches what changed**, so steady-state cost is one
  request per symbol per bar, not per cycle — the single largest reduction from
  v1.

Ways to raise the candle ceiling, in order of value: trade a longer bar (1h
gives 3600 s); add a second provider (Zerodha) on the same `MarketDataSource`
seam; fetch near-signal symbols at a higher cadence than the tail. None require
touching the scanner.

---

## 13. Where to look

| You want to… | File |
|---|---|
| change a provider's declared limits | `capabilities.py` |
| change when a timeframe is due | `scheduler.py` `is_due` / `expected_bar` |
| change request ordering | `queue.py` priority bands |
| change pacing / backoff | `ratelimit.py` |
| add a new provider | implement `MarketDataSource`, register in `for_provider` |
| read market data anywhere | `MarketState` — never fetch directly |
| drive the pipeline | `MarketDataService.poll()` (loop) / `drain()` (one-shot) |
```
