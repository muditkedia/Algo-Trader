# Live Data Pipeline Audit

_Audited 2026-07-20, after the first paper-trading session. Every stage was
traced with an instrumented harness against the real objects; nothing below is
inferred from reading the code alone._

The session reported stale prices and inconsistent market data. The audit found
one root cause for the staleness (F-1), confirmed the scanner was already
correct (F-2), and found the timeframe wiring already strategy-driven but
partly duplicated in config (F-3).

---

## Stage table

| # | Stage | Data in | Data out | Timestamp | Freshness | Failure | Retry |
|---|-------|---------|----------|-----------|-----------|---------|-------|
| 1 | **SmartAPI session** | credentials from `.env` | authenticated client (JWT) | JWT issued at login; keepalive every `session_refresh_seconds` (1800 s) | refreshed on cadence and on any token error mid-request | `SmartApiAuthError` | rate limit: 4 attempts, exponential from 2 s; token error → one refresh + retry |
| 2 | **Instrument resolution** | platform symbol (`RELIANCE`) | `(token, tradingsymbol)` | parquet cache mtime | **self-loading**, cache-first; no expiry within a process | `MappingReport` distinguishes *master unavailable* from *symbol not listed* | one load attempt per process; `reset()` re-arms |
| 3 | **OHLCV fetch** | token, interval, IST window | `[ts, o, h, l, c, v]` rows | IST offsets in payload → `normalize()` → UTC | window = stored coverage end + 1 bar, so stored bars are never refetched | `SmartApiDataError` → status `quarantined` | rate-limit backoff; token error → one session refresh |
| 4 | **Local store** | normalized OHLCV frame | parquet per symbol × timeframe | `date` column (UTC) | `coverage()` → start/end/rows | quality gate → `quarantined`, not stored | none needed — writes are idempotent upserts |
| 5 | **Scanner / feed** | store read, `tail(history_bars)` | frames to the orchestrator | newest bar's `date` | **`freshness()` measures bars behind the expected bar** | empty frame → symbol skipped for evaluation | none |
| 6 | **Strategy evaluation** | prepared frame | boolean entry series | — | only `entry_signal.iloc[-1]` fires | per `(strategy, symbol)` try/except → warn + skip | none; dedup key `(strategy, symbol, tf) → last bar ts` |
| 7 | **Signals** | prepared frame + index | `TradingSignal` | `bar_time` = completed bar's open | inherits stage 5 | untradeable geometry → `None` | none |
| 8 | **Execution** | signal + quantity | `Order` → fill | order `created_ts` / `updated_ts` | paper fills at the bar close (backtest parity) | `BrokerError` | 2 retries + idempotent recovery by `client_order_id` |
| 9 | **Portfolio** | fills | positions, orders, closed trades | `entry_ts`, `saved_ts` | marks from stored close, or LTP for open positions | atomic persist (`os.replace`) | — |
| 10 | **Dashboard** | engine objects (read-only) | 12 JSON snapshots | `generated_at` = **export** time; `data_age_seconds` = **candle** age | both are exported, and the UI shows the candle age | per-file; a locked file is skipped | `os.replace` × 5 attempts (Windows share conflict) |

---

## F-1 — The dashboard reported "current" while prices were hours old

**This was the defect.** `generated_at` on every snapshot is the time the
exporter ran, and the UI compared it to the wall clock. It therefore measured
*the exporter's liveness* and displayed it as *the data's freshness*.

Measured, with the real objects:

```
newest candle in the store   : 2026-07-17 08:30:00+00:00
wall clock now               : 2026-07-20 07:30:43+00:00
ACTUAL market-data age       : 4,261 minutes
dashboard generated_at       : 2026-07-20 07:30:43+00:00
age the UI computed/displayed: 0.0 seconds
```

A 71-hour-old candle was presented as 0 seconds old, and there was no
per-symbol bar age anywhere in the exported snapshots.

**Resolved.** `algo/trading/freshness.py` measures, per symbol, how far the
newest stored bar lags the bar the exchange should have completed by now.
Staleness is counted in **bars**, not seconds, because one bar behind
immediately after a close is normal (the scheduler waits `bar_grace_seconds`
for the feed) while three bars behind at the same moment is a fault.

The verdict distinguishes four states, and the two that were previously
conflated are now separate:

| State | Meaning |
|---|---|
| `FRESH` | within the tolerance of the expected bar |
| `STALE` | market open, more than `stale_tolerance_bars` behind — **shown with the reason** |
| `MARKET CLOSED` | no bar is due; old candles are *correct*, not stale |
| `MISSING` | no candles stored for that symbol at all |

`data_fresh` in the health beat now reports the **fetch**, not the store —
previously it was `bool(latest_prices())`, which stays true for a store that
has not updated in months. That is why the failed session logged
`status: ok, data_fresh: true, errors: 0` for a full day while fetching
nothing and trading four positions on stale bars.

`marks()` returns price **and** bar time together, so a price cannot be
separated from its timestamp by accident.

---

## F-2 — The scanner was already correct

Verified, not assumed (`tests/test_live_scanning.py`):

- a bar is evaluated **at most once** — the dedup key is
  `(strategy, symbol, timeframe) → last bar timestamp`;
- once a newer bar arrives the scanner **advances immediately**;
- a late-arriving older bar **cannot pull it backwards**;
- only `entry_signal.iloc[-1]` fires, so a breakout three bars ago is never
  traded now at a price that no longer exists.

The one subtlety: a store that stops updating makes the scanner go *quiet*
rather than complain. Silence and a calm market look identical. That is
precisely what the freshness report now distinguishes.

---

## F-3 — Timeframes were strategy-driven, but duplicated in config

The scheduler already derived its timeframes from
`orchestrator.timeframes()` (the strategies' own declarations). But
`config.timeframes` was a second, hand-maintained list, and several call sites
read `config.timeframes[0]` — so the data *fetched* and the data *scanned*
could diverge.

**Resolved.** `TradingConfig.effective_timeframes(declared)` makes the
strategies authoritative: config may now only **restrict** the set, never
extend it, because asking for a timeframe no strategy trades would download
data nothing reads. The engine resolves once into `engine.timeframes`, and the
feed, scheduler, cycle default and dashboard all read that.

Also fixed: the live path seeded a symbol with no stored bars using the
ingestion default of **365 days**. Correct for a history download, badly wrong
for a live top-up — adding one uncovered timeframe would have pulled a year per
symbol at the first refresh. The live path now uses `live_lookback_days`
(default 5).

---

## Residual risks

1. **The instrument master never expires within a process.** A symbol listed
   after start-up, or a token that changes, is not picked up until restart or
   an explicit `reset()`. Acceptable for a daily-restart operating model;
   worth a scheduled refresh if the engine ever runs for weeks.

2. **`_manage_open` skips a symbol with no bars.** It cannot evaluate a stop
   without a bar, so a position whose data feed dies stops having its stop
   *checked*. Square-off does **not** depend on that path
   (`tests/test_exit_engine_matrix.py::test_squareoff_still_happens_when_the_symbol_has_NO_market_data`),
   so the position is still closed at the cutoff — but between the feed dying
   and 15:15 the stop is not enforced. The freshness panel now makes a dead
   feed visible, which is the mitigation; enforcing stops on quotes rather
   than bars would be a behavioural change and is deliberately not done here.

3. **Paper fills use the signal bar's close, not the live price.** That is
   intentional — it reproduces the backtest fill convention — but it means
   paper P&L is measured against a reference up to one bar old. Live marking
   (LTP) is now used for *display and P&L only*; no decision reads it.
