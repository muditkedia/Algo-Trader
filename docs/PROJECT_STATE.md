# Project State

_Last updated: 2026-07-23 (STRAT-09 EMA Compression Breakout)_

## STRAT-09 EMA Compression Breakout complete (2026-07-23)

`ema_compression_5m` is the new sole STRAT-09 implementation. It is
bidirectional and enforces a causal four-bar EMA8/20/50 coil, EMA200 and VWAP
bias, a buffered ribbon break, asymmetric same-slot RVOL, candle-body quality,
liquidity, Bollinger contraction, NIFTY EMA20 alignment, ADX expansion, and the
09:30–14:45 window. It owns the compression-range/ATR-capped stop, 1.5R
partial, breakeven, chandelier, EMA20 invalidation, stagnation exit, and
square-off.

Shared directional movement now exposes +DI/−DI/ADX without duplicated math;
NIFTY context exposes EMA20; and persisted pre-TP1 blocking suppresses
secondary trend entries on the same symbol until the partial is booked. Full
details and deviations are in `docs/STRAT09_EMA_COMPRESSION_5M.md`.

Validation: Python compilation succeeded and the complete suite passed with
840 tests. All 16 registered strategies passed the exit matrix and participated
in scanning.

## STRAT-08 VWAP canonical consolidation complete (2026-07-23)

The two legacy long-only 15-minute implementations, `vwap_15m` and
`vwap_pullback_15m`, have been removed and replaced by the sole canonical
`vwap_trend_5m`. STRAT-08 is bidirectional and enforces strict EMA ribbon,
three-bar VWAP slope, shallow current-bar VWAP test, penetration, prior-candle
reversal, asymmetric same-slot RVOL, liquidity, completed 15-minute ADX, wick,
and time gates. It owns the pivot/VWAP stop, 1.5R partial, breakeven,
chandelier, VWAP invalidation, stagnation exit, and square-off.

No shared infrastructure was required. Historical research remains legacy
evidence only. Full details and deviations are in
`docs/STRAT08_VWAP_TREND_5M.md`.

Validation: Python compilation succeeded and the complete suite passed with
829 tests. All 15 registered strategies passed the exit matrix and participated
in scanning.

## STRAT-07 Opening Liquidity Sweep complete (2026-07-23)

`liquidity_sweep_5m` is the new sole STRAT-07 implementation. It is
bidirectional and enforces nearest opening/prior-day boundary sweeps, 0.5ATR
depth, 40% rejection wick, range reclaim, same-slot RVOL, liquidity, RSI,
NIFTY non-confirmation, CPR/prior-level confluence, and the 09:20–10:30 window.
It owns the wick/ATR stop, VWAP-or-1.5R partial, breakeven, directional opposite
boundary, chandelier, VWAP timeout, and square-off.

Execution now supports directional second targets and entry-time level
timeouts. Signals, orders, positions, closed records, recovery, and risk now
support persisted time-bounded blockers, enabling the exact 60-minute
STRAT-01/06 suppression. Full detail is in
`docs/STRAT07_LIQUIDITY_SWEEP_5M.md`.

Validation: Python compilation succeeded and the clean complete suite passed
with 833 tests. All 16 registered strategies participated in scanner and exit
verification.

## STRAT-06 Initial Balance Breakout complete (2026-07-23)

`initial_balance_5m` is the new sole STRAT-06 implementation. It locks the
first six completed 5-minute candles as an immutable 30-minute balance and
enforces buffered boundary acceptance, ATR-normalized width, asymmetric
same-slot RVOL, VWAP, EMA, liquidity, NIFTY’s own IB, CPR clearance, and the
09:45–14:45 window. It owns the midpoint/1.5ATR-capped stop, 1.5R partial,
breakeven, chandelier, VWAP invalidation, stagnation exit, and square-off.

Shared opening context now exposes NIFTY’s causal 30-minute IB. Existing
symbol-level risk suppression blocks STRAT-06 while an earlier opening
position is open but permits it after that position stops out. Full details
and deviations are in `docs/STRAT06_INITIAL_BALANCE_5M.md`.

Validation: Python compilation succeeded and the complete suite passed with
822 tests. All 15 registered strategies passed the exit matrix and participated
in scanning.

## STRAT-05 Gap Fill Failure Reversal complete (2026-07-23)

`gap_fill_failure_5m` is the new sole STRAT-05 implementation. It is
bidirectional and reconstructs the partial morning gap-fill pivot causally,
enforcing gap size, 25%–75% penetration, the 10% no-full-fill boundary,
reversal candle structure, same-slot RVOL, VWAP, liquidity, NIFTY trend, pivot
VWAP confluence, and the 09:25–11:00 window. It owns its pivot/ATR stop, 1.5R
partial, breakeven, chandelier, full-fill invalidation, stagnation exit, and
square-off.

No shared infrastructure was required. The strategy deliberately remains
eligible after a stopped STRAT-04 trade. Full details and deviations are in
`docs/STRAT05_GAP_FILL_FAILURE_5M.md`.

Validation: Python compilation succeeded and the complete suite passed with
812 tests. All 14 registered strategies passed the exit matrix and participated
in scanning.

## STRAT-04 Gap & Go canonical replacement complete (2026-07-23)

The retired long-only `gapgo_15m` approximation has been replaced by
`gapgo_5m`, the sole canonical STRAT-04 implementation. It is bidirectional and
enforces the 1.0%–3.5% gap window, 80% opening-candle retention, directional
opening break, asymmetric same-slot RVOL, VWAP, time, liquidity, EMA, and
NIFTY-gap rules. It owns the specification's collared fill, directional
opening/ATR stop, 1.5R partial, breakeven, chandelier, VWAP invalidation,
stagnation exit, and square-off.

The reusable opening context now exposes causal NIFTY opening-gap percentage.
STRAT-04 reuses the persisted session blocker to suppress STRAT-01/02 after a
Gap & Go entry. Full details and deviations are in
`docs/STRAT04_GAP_GO_5M.md`.

Validation: Python compilation succeeded and the clean complete suite passed
with 802 tests. All 13 registered strategies passed the exit matrix and
participated in scanning.

## STRAT-03 Opening Drive Momentum complete (2026-07-23)

`opening_drive_5m` is the sole registered STRAT-03 implementation. The new
bidirectional 5-minute strategy evaluates the configured opening candle,
enforces body/wick geometry, same-slot RVOL, ATR-normalized range, VWAP,
liquidity, NIFTY, gap, and exact-time gates, and uses the specification's raw
RVOL/body ranking with fixed full-risk sizing.

Orders, positions, closed trades, and risk checks now carry a reusable
session-block group. An opening-drive fill therefore suppresses STRAT-01 and
STRAT-02 for that symbol for the remainder of the session, even after the
drive position closes or after restart. Strategy hooks now support exact raw
ranking and strategy-defined grade sizing while preserving existing defaults.
Full detail and deviations are in `docs/STRAT03_OPENING_DRIVE_5M.md`.

Validation: Python compilation succeeded and the complete suite passed with
801 tests. All 13 registered strategies passed the exit verification matrix
and participated in scanning.

## STRAT-02 canonical replacement complete (2026-07-23)

The retired `first_pullback_15m` approximation has been replaced by
`orb_retest_5m`, the bidirectional 5-minute STRAT-02 state machine. It tracks
the buffered initial break, minimum wave extension, boundary retest, depth and
duration invalidation, continuation trigger, regime/confidence scoring,
grade-dependent target, and active-only STRAT-01 conflict rule. Strategy state
is reconstructed causally from completed bars for deterministic restart
behavior.

Shared opening-session context now lives in `algo.strategies.opening_context`.
Execution declarations support directional structural stop and R-multiple
columns, strategy-specific priority weights, and active-only conflict groups.
Full detail and deviations are in `docs/STRAT02_ORB_RETEST_5M.md`.

Validation: Python compilation succeeded and the complete suite passed with
791 tests. Both `orb_5m` and `orb_retest_5m` passed the full exit matrix and all
12 registered strategies participated in scanning.

## STRAT-01 canonical replacement complete (2026-07-22)

The retired long-only `orb_15m` module has been replaced by the sole canonical
`orb_5m` implementation from the master specification. STRAT-01 is
bidirectional, uses the first 5-minute range and same-slot 10-session RVOL,
requires stock/index/regime/confidence alignment, enters with a bounded limit
collar, and owns its midpoint/ATR stop, 1.5R partial, breakeven transition,
post-partial chandelier, VWAP invalidation, no-progress exit, and session exit.

The execution and trading seams now carry direction and strategy-owned entry/
exit intent end to end. Positions are created only from confirmed broker fills;
working collar orders are reconciled and expire deterministically. Existing
long-only strategies retain their previous defaults. Full detail, including
the two conservative data substitutions and the unavailable sector cap, is in
`docs/STRAT01_ORB_5M.md`.

Validation: Python compilation succeeded and the complete suite passed with
787 tests, including all 12 registered strategies in the exit and scanner
participation matrices.

## Intraday-only strategy baseline (2026-07-22)

Phase 1 of the master intraday-strategy integration is complete. The 23
registered daily/swing strategy modules and their strategy-specific tests were
removed. The auto-discovered production library now contains 12 enabled,
same-session strategies only; all 12 are loaded by the intraday scanner and
forbid overnight holding. Historical research reports and the generic research
framework remain intact for reproducibility, but no removed daily strategy is
registered or executable.

STRAT-01 through STRAT-09 are complete. Sequential implementation continues
with STRAT-10.

## WebSocket market data - locally built candles are PRIMARY (2026-07-22)

Empirically validated (scripts/websocket_validation.py: 100% symbol and
minute coverage at 50 and 300 symbols; scripts/candle_parity_validation.py:
OHLC/volume parity with the Historical Candle API), the production system now
builds candles locally from the SmartAPI QUOTE stream for BOTH paper and live
trading. `market_data_mode: "websocket"` is the default; `"historical"` keeps
the pre-migration REST polling path operational for debugging/validation but
is deprecated for production trading.

- `algo.marketdata.localcandles.LocalCandleEngine` - THE one candle builder:
  exchange-timestamp buckets (1m/3m/5m/15m), cumulative-volume deltas,
  immutable completions, taint-on-gap, bounded memory, thread-safe.
- `algo.data.providers.smartapi.quotefeed.SmartApiQuoteFeed` - packet
  normalizer + tick dispatcher + reconnect/outage bookkeeping.
- `algo.marketdata.streaming.StreamingCandleSource` - locally built candles
  behind the UNCHANGED v2 source seam. Scheduler, MarketState, freshness,
  completion-driven scanning, engine, scanner and dashboard all run as-is.
  The historical API is delegated to ONLY for startup seeding, websocket gap
  repair, validation mode (`ws_validate`), backtesting and research - no
  periodic polling. Position marks come from the tick stream (no REST
  quotes). 1h bars are built locally too, with NSE session-anchored buckets
  (09:15, 10:15, ..., 15:15; the final partial bucket closes at 15:30).
- The dynamic universe is the RUNTIME DEFAULT (run_trading injects
  {"tier": "dynamic"} when no universe block is configured); the watchlist
  states its source loudly (Dynamic / Static) and any fallback is an
  explicit ERROR log, never silent.
- `algo.universe.dynamic` - the daily trading universe: official NIFTY500
  constituents (free-float market-cap top-500 cut, EQ series only, active in
  the instrument master), ranked by the previous session's traded value,
  top 300 selected; versioned under `user_data/universe/dynamic-*.json` with
  a `dynamic_current.txt` pointer; regenerated once per session day and
  hot-reloaded (feed resubscribe) without a restart. Enable with
  `"universe": {"tier": "dynamic"}` in the trading config.

## Current Phase

**Market Data Architecture v2 (the operator's "Phase 7") COMPLETE,
uncommitted.** The market-data path is now a scheduler-driven subsystem with a
single source of truth. Phase 19 (production-readiness pass) complete. Phase 18
(paper-session defect resolution) complete. Phase 17 (fidelity evaluation
mode) complete, uncommitted — awaiting owner approval. Phases 1–16 committed
(…`2ee7957`, `eccfb18`).

## Market Data Architecture v2 delivered — scalable market-data subsystem

Full reference: `docs/MARKET_DATA_ARCHITECTURE_V2.md`. Per-stage benchmark:
`docs/PERFORMANCE_BENCHMARK.md`.

The Phase-6 benchmark found the production bottleneck was the market-data path
itself: one request per symbol every cycle, three independent pollers, and a
cost of `symbols × cycles`. v2 replaces it with `algo.marketdata` — a new
subsystem whose only public surface is `MarketState`.

- **One owner.** `MarketDataService.poll()` is the single fetch path; there is
  no thread, timer or background loop anywhere in the package. The old
  `feed.refresh`, the live tier's private quote timer, and the separate bar
  scheduler are gone. `algo/trading/feed.py` and `algo/trading/scheduler.py`
  were deleted.
- **Scheduler / queue / transport / source** cleanly separated (`what` vs
  `how`). `TimeframeScheduler` emits `DataRequest`s for **only due symbols**,
  **incrementally** (window starts one bar after what is stored — verified on
  the recorded REST call, not the store write), and **never early / never
  skipped**.
- **SmartAPI batching audited against the installed SDK** (smartapi-python
  1.5.5): candles **cannot** batch (one `symboltoken`/request); quotes batch
  **50:1** via `getMarketData` — now implemented (`SmartApiDataProvider.
  fetch_quotes`), replacing per-symbol `ltpData`. The **5000/hour** cap is the
  binding constraint at scale; `check_budget()` flags an over-capacity universe
  at startup.
- **Adaptive rate limiter** — multi-window sliding budget with AIMD, never
  sleeps (answers *when*, caller decides). Replaces fixed-sleep + amnesiac
  backoff.
- **MarketState is the single truth.** Scanner, strategies, risk, execution and
  dashboard read it and nothing else; the dashboard computes no freshness,
  health or portfolio value itself. Freshness stays candle-based (D-039).
- **Data-quality isolation (§10):** 99 configured, 3 failing → the pass still
  completes and scans the 96; freshness names the laggards.
- **Provider abstraction / websocket-ready:** swapping REST for Zerodha,
  Polygon or a replay/websocket transport is implementing one
  `MarketDataSource`; no scanner/strategy code moves. `supports_streaming` is
  the seam.
- **Validation:** all pre-existing tests pass (838 total, 1 skipped); 3 new
  suites — `test_marketdata_scheduler.py`, `test_marketdata_service.py`,
  `test_marketdata_adversarial.py` (44 tests). The adversarial audit found and
  fixed a real delayed-bar defect (a bar is served only when the stored data
  reaches it) and a rate-deferral attempt-budget defect (attempts count real
  fetches, not plan iterations).

## Phase 19 delivered — production readiness from operational feedback (D-039, L-019)

Sixteen items of feedback from the first paper session. Full stage-by-stage
trace in `docs/LIVE_PIPELINE_AUDIT.md`; decisions in D-039.

**The substantive finding** was one category error wearing several faces: the
dashboard reported *the exporter's liveness* as *the data's freshness*. A
candle **4,261 minutes old** was displayed as "0.0 s" old. The same confusion
made `data_fresh` read the store rather than the fetch, which is why the failed
session logged `status: ok` all day while fetching nothing.

- **Freshness (§1, §12)** — `algo/trading/freshness.py` measures per-symbol lag
  against the bar the exchange should have completed, in **bars not seconds**.
  Four distinct states: `FRESH` / `STALE` (with the reason) / `MARKET CLOSED`
  (old candles are *correct* when none are due) / `MISSING`. New MARKET DATA
  panel: configured universe, resolved, live, fresh, missing, failed fetches,
  last successful update, feed status.
- **Minimum trade allocation (§5)** — configurable, default 25% of
  `deploy_today`. Sub-floor positions are **skipped, never padded**; a floor
  must not override a cap. Preflight and the wizard now surface the
  non-obvious consequence: a floor of F caps concurrency at floor(1/F).
- **Timeframes (§3)** — strategies are authoritative; config may only
  *restrict*. Live seeding bounded to `live_lookback_days` (was the ingestion
  default of 365 days per uncovered symbol).
- **Session wizard (§4)** — start-of-day dialogue, in memory only; live mode
  offers the broker's cash. `--no-wizard` for automation.
- **Tiered refresh (§6)** — `live.json` (<1 KB) every second; the rest every
  five. Position cards rebuild only on structural change. `refresh_live()` is
  bounded by open positions, not universe size.
- **Execution plan / square-off / market status (§7, §9, §10)** — deterministic
  if-this-then-that triggers in `TradeManager` evaluation order; per-position
  15:15 countdown; the wall clock replaced by the next-candle countdown.
- **Single runtime state (§11)** — removed two real duplications
  (`open_risk` recomputed in the exporter; `portfolio_value` meaning two
  different things in two panels).

**Verified, not changed:** the scanner (§2) was already correct (traced end to
end); all 12 intraday strategies pass the exit matrix (§8) and square off
unconditionally (§9) — *including when the symbol has no market data*;
participation (§14) is 12 of 35 by design (the other 23 are SWING specs an
intraday engine would close on entry day).

**§13 universe:** the tiered ADTV-ranked architecture already exists. 99
symbols is a *development configuration*, and the binding constraint is DATA:
of 745 candidates only 99 have 15m history (5m has 335). Widening is a
download, not a code change.

**790 tests pass** (was 671); 119 new across freshness, min-allocation, exit
matrix, participation, live tier, scanning, consistency and session setup.

**Open:** live-market verification against real credentials during NSE hours
has not been run — it needs an authenticated session and is the operator's
call.

## Phase 18 delivered — three paper-session defects resolved (D-038, L-018)

The first live paper session exposed three implementation defects. Two shared
one root cause: **the SmartAPI instrument master was never loaded in paper
mode**, so all 99 watchlist symbols failed token lookup while the parsed master
sat unread on disk. Full analysis in `docs/DECISIONS.md` (D-038).

- **Instrument mapping (defect 1).** `SmartApiInstruments` is now the single
  self-loading authority: `_lookup` ensures the master on first use (cache
  first, network only if absent), lookups are O(1) against a normalized index,
  `-EQ`/case/whitespace forms round-trip, a failed load is remembered rather
  than retried per symbol, and `resolve_many()` returns one `MappingReport`
  shared by the provider, the broker adapter, preflight and the dashboard.
  Verified: **99/99 of the real watchlist resolve from the on-disk cache with
  zero network calls.**
- **Market data (defect 3).** Providers may declare `unavailable_reason()`
  (optional, checked before fetching). Ingestion statuses now separate
  `NO_NEW_DATA` (`up_to_date`/`empty`) from `CANNOT_FETCH`
  (`unavailable`/`quarantined`); `diagnosis()` states which in one line.
  `data_fresh` now reports the FETCH, not the store — the session's own logs
  said `data_fresh: true` for a store that had stopped updating, and four
  positions were traded under that banner.
- **Dashboard export (defect 2).** The atomic `os.replace` is retried briefly
  (~150 ms) instead of abandoned — on Windows a reader holding the destination
  blocks it, and the dashboard's own file server was that reader. A lost race
  now SKIPS the write (the next export restores it) rather than raising; each
  snapshot is written independently; staging files are per-PID and swept;
  persistent failures surface on `health.json`.
- **Operator visibility (defect 4).** 99 warnings per cycle → one summary line
  with reason and examples, re-stated only when the situation changes. The
  dashboard gains **TOKEN MAPPING `97 / 99 resolved`** and a **MARKET DATA**
  state (`OK` / `UNABLE TO FETCH` / `OFFLINE`); preflight verifies mapping at
  startup (total failure critical, partial a warning).
- **Test hygiene.** `dashboard_dir` defaults to the real dashboard folder and
  three tests used the default, so a suite run overwrote a live session's
  snapshots. Fixed, plus an autouse conftest guard that content-hashes the
  folder around every test.

**Not touched:** strategies, sizing, risk and execution logic are unchanged —
every edit is in the data/instrument layer, diagnostics or presentation.
**655 tests pass** (was 620); 35 new across `tests/test_instrument_mapping.py`
and `tests/test_dashboard_export.py`.

**Open:** the live-session verification (market data updating with the market
open, against real credentials) has not been run — it needs an authenticated
SmartAPI session during NSE hours and is the operator's call.

## Phase 17 delivered — fidelity mode; the chase is the price of information (D-037, L-017)

Built the isolated research execution engine (`src/algo/research/fidelity.py`;
zero production imports; frozen things untouched): declarative `ExecutionSpec` —
trigger/close/next-open entries, structural stops (OR low, below-VWAP, dip low,
CPR bottom, pullback low), 1×-range / floor-pivot R1-R2 / fixed-R targets, one
partial with breakeven, optional ATR trail, EOD square-off; HONEST gap-through
fills on stops and targets; 11 semantics tests (387 total pass).

- **Part D (99 symbols, matched signals)**: published execution swings the
  trigger-entry baselines hugely positive (orb −12.3→+19.7 net bps PF 1.84, cpr
  −14.0→+13.2 PF 1.96, first_pullback −9.7→+6.8, vwap_pullback −11.4→+2.3);
  vwap_15m unchanged (its published entry IS close-based — internal control ✓).
- **Part E ladder**: d_entry (+17..+35 bps) is essentially the entire effect;
  published stops/targets ≈ nothing (−2..0) — exits-don't-create-edge, 4th time.
- **The control that decides it**: ORB on a TOUCH basis (real resting order, no
  foresight) = **−10.2 net bps** vs +21.0 close-confirmed. The trigger fill
  conditions on the bar's close — unimplementable foresight. The D-036 chase is
  mostly the PRICE OF INFORMATION; the production baseline is the honest one.
- **Part F**: orb/vwap_pullback/vwap_15m class 1 (published form lacks edge,
  realizably executed); cpr/first_pullback class 3 (positive only at the
  unimplementable bound). **All five remain archived; none is a production
  candidate; no AI refinement. The intraday track closes** (cost wall D-026 +
  no realizable published edge). Next: registry R-002 (portfolio mode).
  Report: `user_data/backtest_results/reports/fidelity_eval.md`.

## Phase 16 delivered — baseline fidelity audit (D-036, L-016)

Validation, not improvement: are we testing the published strategies or our
framework's versions of them? Full report:
`research/BASELINE_FIDELITY_AUDIT.md`. Baselines untouched; frozen things frozen.

- **Part A (fidelity):** entry CONDITIONS faithful for all five; the published
  SYSTEMS altered by four framework-wide substitutions — long-only, close-of-bar
  entry, no targets, ATR stops. **All five: category 2 (material differences);
  zero category-1 verdicts.** vwap_pullback additionally over-fires 5–9× (our
  encoding of a discretionary setup).
- **Part B (execution model):** semantics PINNED by 6 new characterization tests
  (entry at signal-bar close; entry bar can't stop out; at-stop GAP fills =
  optimistic; no targets exist; trail arms at +0.6%; partial-day square-off).
- **Part C (costs):** verified vs current Angel One — essentially exact; NSE txn
  sub-rate marginally outdated (0.00297% vs 0.0030699%, ~0.2% of total,
  optimistic); slippage 2 bps/side reasonable here. NOT the loss driver.
  Unchanged per instruction.
- **Part D (universe):** appropriate for all five; the published-practice gap is
  DAY-TYPE conditioning (gap/narrow-CPR/trend day), not symbol choice.
- **Part E (attribution, measured):** gross expectancy ≈ 0 for all five → the
  net loss IS the 12.2 bps cost stack (rank 1); **close-of-bar entry chase
  +14..+44 bps above the published trigger** — the largest recoverable component
  (rank 2); 83–91% of trades ride to square-off as costed noise round-trips
  (rank 3, distribution-shaping); day-type gates absent (rank 4); universe fine
  (rank 5).
- **Part F (readiness):** do NOT start selectivity filters / AI refinement
  against this baseline — it would optimise against the chase artifact. Owner
  decision proposed: a **fidelity evaluation mode** (trigger-level entries,
  published stop/target emulation) as a measurement variant, frozen engines
  untouched.
- Tests: **376 pass, 1 skipped** (6 new).

## Phase 15 delivered — production intraday library, batch 1

Objective shift: from strategy discovery to a production-quality intraday
BASELINE library, backtested and ranked with identical capital/costs/risk. No
optimisation, no tuning — the published logic, exactly. Frozen things stayed
frozen (VALIDATION_RULES, D-031, risk/portfolio/research engines, promotion).

- **Part A — spec locked before coding**: `research/INTRADAY_PRODUCTION_BATCH1.md`
  (rationale, entries, exits, stops, session, no-trade, square-off, assumptions
  per strategy). Exits are PLATFORM-UNIFORM by design (D-006): ATR/structure
  stop → chandelier trail → 15:15 square-off; published fixed targets recorded
  as reference only.
- **Part B — implementation, no duplication**: `orb_15m` and `vwap_15m` already
  implemented the ORB and VWAP-Trend-Continuation specs (Phase 4) and are REUSED
  unchanged. Three new modules: `vwap_pullback_15m` (bounce off rising-VWAP
  support — distinct from vwap_15m's reclaim-after-loss), `cpr_breakout_15m`
  (prior-day Central-Pivot-Range break on volume; new causal
  `central_pivot_range` helper in core/indicators, prior-session-shifted),
  `first_pullback_15m` (first HOLDING pullback after an OR breakout, one entry
  per session). Library now 30 strategies.
- **Part C — validation**: 19 new tests (CPR formula + prior-session causality,
  session-VWAP reset, ORB window/boundaries, gap-up handling, market-open
  no-trade, square-off timing via the simulator, entry-timing on crafted
  sessions, failed-breakout rejection). **370 pass, 1 skipped.**
- **Part D — backtest** (99 NIFTY-100 symbols × 3.5y of 15m bars, identical
  100k/unit, full NSE intraday cost stack, uniform risk limits):
  `user_data/backtest_results/reports/intraday_production_batch1.md`. **All five
  net-negative** — the D-026 intraday-cost verdict reproduced on the production
  library. Expectancy −9.7 to −14.0 bps/trade; win rates cluster 33–38%.
- **Part E — ranking**: **first_pullback_15m is best on nearly every axis**
  (PF 0.68, win 38.1%, exp −9.7 bps, DD 17.1%, Sharpe −6.14); cpr_breakout 2nd
  (most selective, 13.2k trades); orb 3rd; vwap_15m 4th; **vwap_pullback last by
  an order of magnitude** (115k trades — turnover × fixed cost is destiny).
  Lesson for the AI-improvement phase: SELECTIVITY is the lever; cut false
  signals, never add entries. Nothing promotable; paper engine stays off.

## Phase 14 delivered — R-001 executed under governance (D-035)

First research execution under the governance process. Pre-registered
(`research/PREREGISTRATION_R001.md`) 3 INDEPENDENT short-horizon families (no
sweep), implemented via the frozen hypothesis framework (`scripts/research_r001.py`),
measured on NIFTY-500 under the frozen gate. Report:
`user_data/backtest_results/reports/r001_league.md`.

- **All 3 FAIL, but informatively (Part D):**
  - **expiry (F&O expiry week): INCONCLUSIVE, the best short-horizon result in the
    project** — selection +74.4 bps point (clears cost), beats random (rel-PF
    1.35), 54k signals — yet CI-low −1.6 (short of significance).
  - gap-fade: FALSIFIED (−45) — NSE down-gaps continue, don't fade.
  - month-start: rejected (+18) — the turn-of-month edge is pre-END anticipation
    (tom), not month-START inflow.
- **Finding:** even at MAXIMUM power (54k signals, 3-7d), a per-trade short-horizon
  selection edge does not certify — the drift/noise floor of per-trade NSE
  selection. The short-horizon per-trade thesis is REFUTED for certification.
- **Recommendation (Part E): ARCHIVE R-001, proceed to R-002 (portfolio-mode)**,
  carrying the directionally-real expiry effect as R-002's priority test case (a
  monthly basket's ~130 near-independent returns may certify where per-trade
  can't). Not a data pause; not more per-trade R-001 variants (parameter-chasing).
- 30 strategies now on record; nothing certified; paper engine off. 351 tests.

## Phase 13 delivered — research governance (D-034)

Platform + validation framework are stable/frozen. This phase made the research
PROCESS permanent policy (docs only; no code, no strategies, no optimisation).

- **`docs/RESEARCH_REGISTRY.md`**: single source of truth for every idea. No idea
  is built before it exists here. 11 active entries (R-001..R-011) for the
  post-mortem's unexplored directions + an archived map of the 27
  explored/rejected by family. Ranked backlog.
- **`docs/RESEARCH_STANDARDS.md`**: permanent policy — pre-registration,
  measurable/falsifiable hypotheses, the frozen gate as the ONLY success
  criterion, reproducibility, promotion workflow, negative-result archival — plus
  the Hypothesis Quality Framework (7-dim weighted rubric; power ×3, independence
  ×2.5) that ranks research ORDER, never predicts a PASS.
- **Backlog:** R-001 short-horizon (4.24, zero new infra) ≈ R-002 portfolio-mode
  (4.28, owner-gated new mode) at the top; then R-003 earnings, R-004 index flows,
  R-010 sector-relative.
- **Readiness (F): READY.** Governance loop closed (idea → registry → prioritise →
  pre-register → compile → measure → archive). Residual process items (no new
  infra): enforce multiple-testing discipline as throughput rises; reserve an
  out-of-sample holdout before screening; R-002's portfolio mode is the one
  genuinely-required new capability (owner-gated). 351 tests pass.

## Phase 12 delivered — post-mortem + pivot to automated research (D-033)

The validation framework is FINAL/frozen. 27 strategies measured, 0 certified —
a completed research result. This phase improved the PROCESS
(`research/POSTMORTEM.md`).

- **Post-mortem (A)** by family: cross-sectional RANK signals are directionally
  real (large positive point edges) but uncertifiable at 21-126d; per-symbol
  LEVEL signals carry no selection edge; low-vol is INVERTED here; tom (calendar,
  3-7d, CI-low −7.9) is the lone near-certification. No family exhausted as a
  phenomenon; all exhausted as long-horizon per-trade tradeables under the gate.
- **Knowledge gaps (B)**: short-horizon microstructure, event-driven (needs
  data), portfolio-level factor evaluation, ensembles, regime allocation,
  inter-market, adaptive selection.
- **Framework (C) — IMPLEMENTED**: `research/hypothesis.py` + `components.py`.
  Hypotheses are DECLARED from reusable components; `compile_hypothesis` →
  measurable `StrategyProfile` on the existing seam, frozen gate untouched.
  Equivalence-tested (compiled == hand-written, bit-identical). `enabled=False`
  research objects — no live strategy added. Grids are loops.
- **Composition (D) — DESIGN ONLY**: rank aggregation / voting / portfolio-mode
  (the strongest lead: monthly-rebalanced decile → ~130 near-independent returns
  may certify where per-trade cannot; a new MODE, not a gate change).
- **Roadmap (E)**: `IMPLEMENTATION_ROADMAP.md` §0 — ranked #1 short-horizon
  effects → #2 portfolio-mode → #3 event data → #4 ensembles → #5 throughput.
- **Assessment (F)**: framework sufficient for judging; low long-horizon power is
  a DATA property; more history is marginal (11y ≈ API limit); **project ready to
  transition from manual strategies to automated hypothesis generation +
  screening**. 351 tests pass (+7).

## Phase 11 delivered — bigger corpus, the cross-sectional class (D-032)

The methodology is frozen (D-031). Phase 11 attacks the POWER problem (L-011) via
data + the strategy class the gate was built to judge.

- **Corpus (Part A)**: daily research corpus expanded 99 NIFTY-100 × 3.5y →
  **500 NIFTY-500 × up to 11.5y** (2015→2026), reusing the D-029 backfill: 401
  new, 97 backfilled, 0 quarantined, 0 excluded. Median ~11.3y; 33 short = real
  recent IPOs. **Independent 20-day blocks/symbol ~44→~143 (×3.2); usable
  breadth 99→467 (×4.7).** Intraday left at NIFTY-100/3.5y (batch 2 is daily).
  NIFTY-500 = committed dated NSE snapshot. Report:
  `scripts/corpus_report.py`.
- **Cross-sectional seam**: additive `StrategyProfile.prepare_cross_section`
  hook (default no-op) + `strategies/cross_section.py` primitives + a DRY decile
  base. The per-symbol path (all incumbents) is bit-identical; the frozen gate
  and measurement are untouched. Lookahead-safe (truncation-tested).
- **Batch 2 (Parts B-E)**: removed 6 rejected-idea variants; pre-registered
  (`research/PREREGISTRATION_BATCH2.md`) and implemented **13 strategies**
  (11 cross-sectional + tom + stage2) spanning every requested family — the
  first cross-sectional strategies on the platform.
- **Validation**: 21 new cross-section tests (seam additivity, decile ranking,
  lookahead, market/breadth aggregates) + batch-2 metadata; full suite green.
- **Measurement (Parts F-H): all 13 FAIL** on NIFTY-500 (report:
  `user_data/backtest_results/reports/batch2_nifty500_league.md`; NIFTY-100
  cross-checked). But NEW vs batch 1: the cross-sectional selection POINT edges
  are large, positive and CONSISTENT (illiq +392, stage2 +383, hi52rank +252,
  bab +232, xsmom +149…), 11/13 beat random (rel PF > 1) — **the factor effects
  are directionally real on NSE**. They FAIL only because the 60-126-day
  selection CIs are enormous (−300 to −1070 bps): even 3.2× the periods can't
  power a long-horizon CI. **tom_daily is the standout** (CI-low −7.9, closest
  of all 26 strategies) — short horizon + 53k signals. All 13 → `rejected`;
  incumbents untouched (27 on record). Nothing promotable; paper engine off.
- **Recommendation (H)**: the binding constraint is statistical power at long
  horizons on one market, not strategy choice. Highest-value next step (no
  methodology change): **shift to SHORT-horizon high-frequency effects**
  (the tom lead), where the sample certifies — not more multi-week factor sorts.
  See D-032 / L-012.

## Phase 10 delivered — the benchmark amendment (D-031, L-011)

Closed the L-010 defect permanently. The promotion gate is no longer absolute
return but the **selection edge over a random entry**, gated on the D-028
block-bootstrap CI of the difference (VALIDATION_RULES §26, owner-approved).

- **Amended gate** (`edge_lab` + `engine.verdict_for`): selection edge =
  strategy forward return − random-entry baseline; PASS requires its CI-low to
  clear cost, exactly as D-007 required of the absolute edge. Absolute §7 bars
  retained; the amendment only tightens (a strategy failing every absolute bar
  can't be raised; one with no selection edge can't pass however strong its
  drift-fed absolute numbers).
- **Benchmark battery** (`research/benchmarks.py`): buy&hold matched-per-trade +
  portfolio; random entry (3 seeds, matched count); random matched-holding — all
  through one shared `simulate_entries`, deterministic, with excess-vs-B&H,
  excess-vs-random, information ratio, relative PF/DD (+CIs).
- **Re-judged the whole DB** (`scripts/rejudge_benchmark.py`, new evaluation
  generation, history kept): **all 14 FAIL, 5 status changes** — wyckoff_spring,
  hvol, triple_screen `measured`→`rejected`; donchian55, tsmom `draft`→
  `rejected`. The batch-1 "winners" have positive selection POINTS (+64/+110/+23
  bps) but CI-lows of −108/−82/−129 bps: **not distinguishable from a random
  entry** on 3.5y of multi-week data. Report:
  `user_data/backtest_results/reports/benchmark_rejudge.md`.
- **Validation**: drift-control regression test FAILs (the L-010 defect, now an
  automated guard); planted-selection control PASSes; intraday incumbents
  reproduce D-026 to the decimal; determinism unit-tested. **285 tests (+8).**
- **NOTHING is promotable.** The paper engine stays off — now not by policy
  (D-030) but by evidence: zero strategies show established selection skill.

### Is the framework stable? (Part F)

The gate is correctly SIZED — validated that market drift cannot pass it — so
remaining strategies can be measured under it without further protocol change
for SAFETY. But it has low POWER on short samples (the unpaired difference
carries drift variance). Before batch 2 is worth running, the recommended (owner-
approved) refinement is the **paired date-matched cross-sectional selection
edge**, which cancels drift per-observation. See D-031 / L-011 and §"Open
decisions" below.

## Phase 9 delivered — batch 1: eight strategies, measured, and the control that reframes everything (D-030, L-010)

- **Pre-registered** (research/PREREGISTRATION_BATCH1.md, written before any
  code): tsmom, hi52, egap (PEAD proxy), donchian55, wyckoff_spring, squeeze,
  hvol, triple_screen — eight distinct hypothesis families, all 1d/long-only/
  delivery, horizons declared per strategy, platform-uniform exits.
- **Implemented as eight drop-in modules** — discovery, measurement, evidence,
  ranking and promotion all picked them up with ZERO platform edits (the
  Phase-8 promise, kept). One genuine logic bug caught by tests pre-measurement
  (spring's reclaim reference collapsing to the flush low). Shared weekly
  resample/as-of join added to core/indicators with a truncation lookahead
  proof for both weekly-screen strategies.
- **Measured on the real corpus** (99 symbols, production evidence DB):
  **PASS** wyckoff_spring (PF 1.44), hvol (1.29), triple_screen (1.29) →
  `measured`; **BORDERLINE** donchian55, tsmom → `draft`; **FAIL** hi52,
  squeeze, egap → `rejected`. Full table + diagnostics:
  user_data/backtest_results/reports/batch1_league_table.md.
- **THE CONTROL FINDING (L-010): 2 of 3 seeded random-entry strategies also
  PASS the frozen bars on this corpus.** At 40–60-day horizons the
  absolute-return D-007 gate saturates on bull-market drift + survivorship
  (today's constituents). Batch-1 PASSes are therefore NOT deployment
  evidence. The informative statistic is the SELECTION edge vs the random
  baseline: hvol **+91 bps** and wyckoff_spring **+67 bps** at 20d (both clear
  the 30.9 bps cost), triple_screen +22 (does not), donchian ≈0, tsmom −146
  (its entire "edge" was drift), squeeze −202 (the pre-registered failure).
- **DO NOT START THE PAPER ENGINE**: three `measured` statuses exist, so it
  WOULD start — D-030 forbids it pending the gate amendment.
- **Proposed for owner approval (Phase 10)**: amend the frozen protocol with a
  drift-adjusted gate leg (selection edge vs random baseline must clear cost
  on its CI) + standing seeded random controls in every real measurement.
- Reproducibility verified (re-run: 0 new signals, verdict identical to the
  decimal); six incumbents untouched; **277 tests pass** (37 new).

## Phase 8.5 delivered — research planning layer (D-028, D-029)

- **`research/IMPLEMENTATION_ROADMAP.md`**: every candidate scored on twelve
  evidence dimensions and tiered (IMPLEMENT FIRST: B1 time-series momentum,
  A3 52-week high, D2 earnings-gap continuation · SOON: B3, H1, C2, D3, B7,
  then the cross-sectional seam, then A1, E1 · LATER: 10 · NOT FEASIBLE: 8 with
  named unblockers). Diversified ten-strategy queue with effort / research
  value / qualitative survival probability / failure risks / dependencies per
  strategy. Survival ceiling deliberately "moderate" — calibrated to the 0-for-6
  base rate and the corrected D-028 gate.
- **Part C practitioner audit** (library §3): added **B7 Elder triple screen**
  and **H1 Wyckoff spring** (objective components isolated; non-codable
  narrative parts explicitly excluded); expanded **B3** with the mechanical
  Turtle/Darvas rules (pyramiding documented as NOT integrable); mapped
  CAN-SLIM / institutional ORB / RS-leaders onto existing entries so they are
  never double-counted. Library now 29 candidates.
- **D-029 — RELIANCE/TCS root cause found and fixed**: a 1d-only pre-flight
  smoke test seeded the two symbols from 2025-01-01, and
  `incremental_update` only ever extended coverage FORWARD, so the full
  2023 download reported them `up_to_date` while silently missing 2023–24
  (evidence: mtime forensics — the pair was written 14s before the alphabetical
  batch and never touched again; 1h/15m were full because the smoke test was
  1d-only). Fixed with head-gap backfill (loud `backfilled` status; no-start
  callers like the paper engine unchanged); both symbols repaired live to the
  full 877 bars; store audit clean (only JIOFIN late — genuine listing date).
- **D-028 — stationary block-bootstrap CIs for long horizons**, reusing
  `monte_carlo.py`'s existing implementation. Verified on the real corpus: all
  six verdicts identical, 15m CI-lows bit-identical, **ema200_daily CI-low
  23.4 → 6.2 bps / nr7_daily 4.0 → −13.0 bps** — the day bootstrap had been
  overstating long-horizon confidence ~17 bps. Promotion rules untouched.
  Synthetic-overlap widening + planted-PASS controls added.
- Validation: **240 tests pass** (9 new: 4 backfill, 5 bootstrap). Real-sweep
  verification 303s. Next: implement B1 (time-series momentum) ALONE.

## Phase 8 delivered — the research pipeline (D-027)

The bottleneck is no longer infrastructure; it is finding a strategy with an edge
that clears costs. Phase 8 made adding a candidate cheap **without changing a
single verdict**.

- **One seam**: `ResearchEngine.research` / `research_all` runs the whole loop
  (register → record signals w/ confidence → label outcomes → measure edge →
  simulate → evaluate → cost sensitivity → calibration → verdict). It was
  hand-wired in `run_measurement.py`; a second entry point would have duplicated
  it. `_judge` is the single implementation of the evaluation.
- **Strategies are discovered, not listed**: `algo.strategies.library` discovers
  its own modules via the existing `StrategyRegistry`. **Adding a candidate is
  one file** — the hand-maintained `ALL_STRATEGIES` tuple is gone. Name
  uniqueness is enforced at import instead of by a test.
- **Pre-registered horizons**: `meta.horizon_bars` / `meta.max_hold_bars`,
  defaulting to exactly the Phase-5/7 values (1/2/4/8, 8-bar hold). This closes a
  real gap — the horizon was hardcoded platform-wide, so a candidate needing
  weeks could only be measured over 8 bars. **No CLI flag exposes it**: choosing
  a horizon after seeing a verdict is the tuning D-026 forbids.
- **Throughput, measured not assumed**: the suspected bottleneck (indicators
  recomputed 3×) was **0.4%** of the run. The real cost was `record_signals` at
  **54%** — a transaction (disk sync) + a SELECT per signal. Reusing the logger's
  existing batch writer cut it **14.77s → 0.60s (24×)**, the pipeline **2×**, and
  a re-run to **0.04s**. (D-025's labeling fix, applied to the recording step it
  had left per-row.)
- **`--strategies` subset filter**: iterating on one candidate no longer
  re-measures the five on record.
- **`research/reporting.py`**: league table + verdict detail promoted out of the
  script, so every entry point renders identical evidence.

### Phase 8 validation

- **231 tests pass** (25 new; baseline 206 unchanged).
- **The decisive proof — D-026 reproduces exactly.** Re-running the full real
  sweep (99 symbols, 2.85M bars, 6 strategies) through the new pipeline against a
  copy of the production evidence DB returns **every verdict and every headline
  number identical** to the recorded D-026 table (signal counts to the unit; PF,
  edge, CI-low, cost to the recorded precision), in 192s, with
  `recorded=0 labeled=0` — proving idempotency on real data.

## Phase 8 research report — `research/CANDIDATE_LIBRARY.md`

27 candidates across momentum, trend, volatility compression, events, low-risk,
mean reversion and seasonality, each with hypothesis, horizon, weaknesses,
cost sensitivity and references (including deliberate counter-references).

**The organizing finding:** cost is fixed per round trip (~31 bps delivery, ~12
intraday) while edge scales with the move, so the D-007 hurdle as a *share of the
target move* is the survival metric. The library is therefore biased to
multi-week/multi-month holds — which is D-007's own Option 2 and L-006's own
conclusion, now backed by NSE evidence.

**Recommended first implementation: time-series (absolute) momentum** — most
replicated effect in the literature, implementable on the current interface with
zero platform change, nearly parameter-free (so a FAIL indicts the market, not
the encoding), and aimed at the one real opening in the evidence: at the longest
horizon ever measured here (8 bars), ema200_daily's edge came within 4% of the
bar.

### Open decisions this raised (flagged, NOT taken)

1. ~~Block-bootstrap CIs before judging any long-horizon candidate~~ —
   **RESOLVED in Phase 8.5 (D-028)**, verdict-preserving, verified on the real
   corpus.
2. **A cross-sectional seam.** `entry_signal(dataframe) -> Series` is per-symbol,
   so cross-sectional momentum / relative strength / low-vol ranking — the
   best-documented, most cost-survivable families — **cannot be expressed**. The
   fix is additive (an optional `prepare_cross_section` the engine calls once per
   sweep), not a redesign. Out of scope for Phase 8.
3. **Multiple testing.** Evaluating ~25 candidates at a 95% bound yields ~1.25
   expected false PASSes from noise alone, and `EdgeReport.best()` already picks
   the best of several horizons without adjusting for that choice. A
   high-throughput pipeline without this control is a false-discovery factory
   (Harvey/Liu/Zhu 2016).
4. **Data gaps that block whole families**: no sector map (`instruments.sector`
   **0/99**), no earnings calendar (blocks PEAD — the highest-value acquisition
   available), no index-membership history, long-only (blocks pairs trading).
5. ~~DATA DEFECT: RELIANCE and TCS truncated~~ — **RESOLVED in Phase 8.5
   (D-029)**: root cause was the forward-only incremental update after a
   1d-only smoke-test seed; pipeline fixed with head-gap backfill; both symbols
   repaired to full 877-bar coverage; store audit clean.
6. **`ARCHITECTURE.md` is stale** — it still documents the retired Freqtrade
   crypto system (Binance spot, 5m/15m/1h/4h, `user_data/strategies/algo_core/`),
   which D-009 retired. `SYSTEM_OVERVIEW.md` is **empty**. The Indian-equity
   architecture is documented only across DECISIONS/PROJECT_STATE.

### Known limitation, deliberately not fixed

`label_outcomes` and `simulate_strategy` each run `simulate_trade` over every
signal — the same simulation computed twice, ~85% of the remaining runtime. They
are not trivially unifiable (the labeler uses raw bars + a fixed ATR(14); the
simulator uses the prepared frame and honours a strategy-supplied `atr`), and
resolving it changes measurement, which this phase froze. Worth its own phase.

## SECURITY EVENT (2026-07-17) — contained, no leak

Live SmartAPI credentials were placed in the **tracked** `.env.example`, and a
second copy existed in an un-ignored `smartapi.env`. Verified: **the secrets
were never committed to any branch** (`git log -S` across `--all` returns
nothing; `HEAD`'s template was still empty). Remediation: secrets moved to the
git-ignored `.env`, the tracked template restored to empty placeholders, and
`.gitignore` widened to `*.env` / `.env.*` (with `!.env.example`) so no `.env`
variant can ever be staged. `smartapi.env` is now ignored but is a redundant
plaintext copy — **recommend deleting it** (`.env` holds everything).
No rotation is required, since nothing was published.

## Phase 7 delivered — Part A: production execution rules

- **A1 max 3 positions** (`PortfolioConfig.max_open_positions = 3`): a fourth
  opportunity is decided against the book — OPEN / SKIP / REDUCE / REPLACE —
  with the reasoning persisted to evidence.
- **A2 one capital pool** (`max_capital_deployed = 1.0`): dynamically allocated
  across the three slots; capital idles only when a configured risk limit binds.
- **A3 buying power, never hardcoded** (`paper/buying_power.py`): cash |
  multiplier | broker modes; `SmartApiSession.rms_limits()` reads the real
  `rmsLimit` funds; `max_multiplier` guard against a mis-parsed field. Leverage
  raises the notional ceiling; **risk stays equity-based** (D-024).
- **A4 net break-even** (`risk/breakeven.py`): stop moves to entry grossed up by
  brokerage + STT + exchange + GST + stamp + SEBI + slippage + buffer (from the
  configured CostModel), and only once a configurable trigger is met (default
  3× round-trip cost) — never immediately after entry (D-023).
- **A5 trailing modes** (`risk/trailing.py`): promoted ATR chandelier stays the
  default; percentage / structure / volatility added behind one interface;
  profit-lock ladder applies in every mode; strategies opt in via declarative
  `meta.trail_mode` — **no strategy code changed**.
- Removed a duplicate `daily_risk_budget` field (PortfolioConfig owns it).

Part A validation: **200 tests pass** (21 new).

## Phase 7 — Part B: real market (COMPLETE)

- **SmartAPI login: VERIFIED** against the live API (session → profile
  `MUDIT KEDIA`, exchanges `nse_cm`/`bse_cm` → refresh → logout).
- **Universe**: `scripts/build_universe.py` generates `nifty100.txt` from the
  live instrument master (2,406 NSE `-EQ`) → **99/101 active**. `TATAMOTORS`
  (demerged; successor `TMPV`) and `LTIM` are absent — genuine index drift, not
  substituted (that would be inventing index membership).
- **Historical download: 99/99 symbols on all three timeframes**,
  **2,846,753 real NSE bars**, 2023-01-01 → 2026-07-17
  (1d 85,651 · 1h 604,058 · 15m 2,157,044).

### Two real defects the live run exposed (both fixed, both tested)

1. **Rate limiting mistaken for bad data.** 47 of 75 quarantines were
   `Access denied because of exceeding access rate` — the API enforces harder
   than its documented 3/s. The SDK surfaces it as a JSON *parse* error, so it
   looked like corruption. Fix: detect it, exponential backoff + retry,
   conservative 1.0s default throttle. Recovered 1h 74→99 and 15m 55→99.
2. **One bad bar discarded a whole symbol.** SmartAPI returns rare impossible
   bars (BRITANNIA **1 bad in 21,829** = 0.005%; CANBK 4 in 21,828). The
   all-or-nothing gate threw away 3.5 clean years over one tick. Fix: an
   explicit, counted, logged row-level drop with a
   `max(max_invalid_rows, pct × rows)` tolerance — the absolute floor matters
   because 1 bad bar in an 877-row *daily* series is 0.11% while the identical
   defect in 15m data is 0.005%. Systematically broken feeds are still
   quarantined whole. Recovered 1d 93→99.
3. **Labeling was O(signals × bars)** (a linear frame scan per signal ≈ 1.3
   billion comparisons) plus one disk sync per row. Fix: a date→row index built
   once per symbol + batched writes. ~12 outcomes/s → 62,166 in minutes.

### REAL measurement result: ALL SIX STRATEGIES FAIL

99 symbols · **125,794 signals · 125,719 labeled outcomes** · no tuning.

| strategy | verdict | signals | PF | expectancy | edge bps | CI-low bps | cost bps |
|---|---|---|---|---|---|---|---|
| ema200_daily | **FAIL** | 1,402 | 1.20 | +0.00248 | 59.4 | 23.4 | 30.9 |
| nr7_daily | **FAIL** | 898 | 1.03 | +0.00036 | 50.1 | 4.0 | 30.9 |
| volexp_1h | **FAIL** | 11,086 | 0.69 | −0.00121 | 9.1 | 2.4 | 12.2 |
| orb_15m | **FAIL** | 23,706 | 0.66 | −0.00123 | 3.0 | 0.3 | 12.2 |
| vwap_15m | **FAIL** | 26,517 | 0.58 | −0.00124 | 2.8 | 0.1 | 12.2 |
| pullback_15m | **FAIL** | 62,185 | 0.52 | −0.00149 | 0.2 | −2.0 | 12.2 |

All strategies are `rejected` in the evidence DB; **the paper engine refuses to
start (exit 3)** — the gate works.

**What the evidence says (not opinion):**
- **The intraday strategies are killed by costs, exactly as crypto was**
  (D-007/L-006 reproduced on Indian equities): 0.2–9.1 bps of gross edge
  against a 12.2 bps round trip. MFE/|MAE| 1.03–1.16 — barely better than a
  coin flip. Sharpe −4 to −11.7. These are not close.
- **The daily strategies have REAL edge but it does not clear delivery costs
  with confidence**: ema200_daily's 59.4 bps point edge exceeds the 30.9 bps
  cost and its expectancy is *positive* (PF 1.20), but the 95% CI lower bound
  (23.4 bps) sits below cost, and PF misses the 1.25 floor. It is the only
  candidate worth further *research* — not deployment.
- **Confidence carries no signal**: correlation with realized P&L is −0.026 to
  +0.018 across all six. The Phase-4 heuristic scores are, on this evidence,
  worthless — precisely the L-003 finding, now reproduced on NSE. They must be
  replaced by evidence-calibrated priors, not hand-tuned.

**The project's production market-data source is Angel One SmartAPI** (D-020).
Kotak Neo is superseded (code retained, unused). CSV import remains a fallback,
not the primary path. Still **no live orders anywhere** — paper trading only.

## Execution-intelligence extension (D-022)

The scanner + paper engine were extended into the full production pipeline:

- **Canonical `Opportunity`** (scanner/base.py): prices + risk geometry from
  the promoted risk engine, expected R/R/return/holding, historical win rate /
  expectancy from evidence, confidence component breakdown, regime (daily
  labeler, cached), liquidity metrics, estimated NSE cost, reason, evidence
  reference — the standard object through scanner → ranking → execution.
- **Ranking engine** (scanner/ranking.py): configurable `RankingWeights` +
  `RankingScales` (nothing hardcoded); components = expected return, hist
  expectancy/win rate, confidence, calibration correlation, liquidity,
  reliability (sample size), cost penalty, risk/reward; missing evidence →
  neutral midpoint. **Every ranking decision persisted** (signal rank +
  `_ranking` breakdown; re-scans update rank, never duplicate).
- **Adaptive scan scheduler** (scanner/scheduler.py): per-timeframe cadence
  (15m≈45s, 1h≈3min, 1d≈7min — configurable); management never waits on scans.
- **Portfolio manager** (paper/portfolio.py): OPEN / SKIP / REDUCE / REPLACE
  against capital, deployed-exposure cap, max positions, sector caps
  (correlation proxy until return-correlation evidence exists), daily risk
  budget (soft/hard), replace-if-better margin. SKIPs recorded to evidence.
- **Dynamic sizing** (paper/sizing.py): risk-parity core (`risk_based_stake`
  reuse) × confidence multiplier, capped by risk budget, available capital,
  concentration and hard stake limits; below-minimum → 0 (honest skip).
- **Continuous loop** (paper/engine.py + scripts/run_paper.py): every tick
  manages positions (stops → trailing ratchet → 15:15 IST square-off); scans
  run only when due; JSON state resume; risk budget rolls at the IST day.
- **Console dashboard** (paper/dashboard.py): market status, strategies,
  universe/eligible, scan progress, open positions with unrealized PnL,
  capital deployed/available, realized PnL today, risk budget left, top
  ranked opportunities, health. Monitoring only.
- `evidence/queries.py`: shared read-side stats (one SQL home for calibration
  correlation + per-strategy overall stats — no duplication).

Extension validation: **179 tests pass** (19 new: scheduler cadence/force,
ranking weight-sensitivity/evidence-neutrality/bounds, every portfolio
decision path, every sizing cap, dashboard sections, market-status clock,
management-without-scan, portfolio-skip evidence, snapshot). End-to-end
offline demo: scan → 3 ranked candidates → 2 positions opened with sized
stakes → dashboard rendered → persisted ranking breakdown verified.

## Phase 6 delivered

- **SmartAPI provider** (`src/algo/data/providers/smartapi/`), built against
  the official SDK (`smartapi-python`/`SmartConnect`, verified from source):
  - `config` — credentials from `.env`/environment ONLY (stdlib loader in
    `core/config.load_env_file`), secrets masked, missing credentials reported
    by name with portal provenance;
  - `session` — login (`generateSession` + TOTP via pyotp), refresh
    (`generateToken`), `getProfile`, `terminateSession`; lazy SDK import,
    injectable client;
  - `instruments` — official scrip master JSON → NSE `-EQ` symbol/token map,
    parquet cache (works credential-free; **verified live: 2,408 NSE
    equities**);
  - `provider` — `getCandleData` chunked per documented per-interval day
    limits, throttled (~3 req/s), IST→UTC normalization, one refresh-retry on
    token expiry; `ltpData` latest quote. Supports 1m…1d incl. 15m/1h/1d.
- **Historical downloader** (`scripts/download_history.py`) — reuses
  IngestionEngine/store wholesale: incremental by coverage, resume = re-run,
  duplicate-proof, quality-gated, quarantine reporting.
- **`scripts/smartapi_login_check.py`** — 5-step verification; graceful named
  missing-credential report (exit 2) without credentials.
- **`scripts/run_measurement.py --store-dir`** — production measurement path:
  real store + real evidence DB; verdicts persist as lifecycle statuses
  (PASS→measured, FAIL→rejected; synthetic runs never advance a strategy).
- **Paper Trading Engine** (`src/algo/paper/engine.py` +
  `scripts/run_paper.py`) — bar-driven: manage (stop → ratchet → 15:15 IST
  square-off) then scan → select top-N → open; JSON state resume; every
  signal/trade recorded to evidence `mode=paper`; **gated: refuses strategies
  that have not survived measurement on real data** (`--allow-unmeasured` for
  offline validation only). NO live orders.
- `.env.example` rewritten for SmartAPI with exact portal provenance.
  `pyproject.toml` gains the optional `smartapi` extra.

## Validation performed (credential-free, per Task 4)

- `pytest`: **160 passed** (19 SmartAPI mocked: .env loading, auth flows +
  failure paths, instrument filtering + cache, candle parsing IST→UTC,
  chunking count, throttling, token-refresh retry, graceful unknowns,
  ingestion wiring + duplicate protection; 6 paper: gating refusal/override,
  open/stop-loss close, square-off, state resume, capacity + confidence floor).
- Live credential-free check: official instrument master downloaded (2,408
  NSE equities). Scripts exit gracefully with named missing variables.
- Offline end-to-end paper pipeline on synthetic store: signal → position →
  evidence → restart resume. Measurement regression green; statuses persisted.

## The moment credentials exist (no code changes needed)

1. `scripts/smartapi_login_check.py` — verify login.
2. `scripts/download_history.py --symbols-file <file>` — populate the store.
3. `scripts/run_measurement.py --store-dir user_data/data/nse` — real verdicts.
4. `scripts/run_paper.py --symbols-file <file> --loop` — paper trade the
   survivors (engine refuses if none survive — that is by design).

## Prior phases

P1 foundation · P2 data/scanning · P3 Kotak (superseded) · P4 six strategy
candidates · P5 measurement machinery (controls: planted edge PASSes, noise
FAILs; all six FAIL on synthetic — correct). See DECISIONS D-008…D-021.

## Open decisions / actions needed from owner

- **Approve committing Phase 10** (the benchmark amendment). Phases 1–9 are
  committed.
- **Decide the power problem before batch 2** (the load-bearing decision). The
  amended gate is correctly sized but low-power on 3.5y multi-week data. Options,
  in EV order: (1) approve the **paired date-matched cross-sectional selection
  gate** (D-031/L-011 — cancels drift per-observation, far more power); (2)
  acquire an **earnings calendar** (unblocks PEAD) and a **sector map** (unblocks
  rotation) — different, larger-per-trade hypotheses; (3) accumulate more
  independent history (years, for multi-week horizons). Implementing more
  technical variants now would only produce more within-noise FAILs.
- `.env` exists; the `smartapi` extra is installed; NIFTY-100 universe chosen.

## Open blockers

- **Nothing is promotable.** All 14 strategies are `rejected` under the amended
  gate — none shows an established selection edge on the available data. The
  paper engine correctly refuses to start (by evidence, not policy). Progress
  requires the power decision above, not more strategies.
