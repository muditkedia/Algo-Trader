# Decisions

Architecture and strategy decisions, with the evidence behind them. Newest first.

---

## D-039 — Production-readiness pass: freshness is a property of the candles, not of the exporter (2026-07-20)

Sixteen items of operational feedback from the first paper session. The
substantive finding is one category error with several faces; the rest is
plumbing that follows from fixing it. Full stage-by-stage trace in
`docs/LIVE_PIPELINE_AUDIT.md`.

### The category error

`generated_at` on every dashboard snapshot is the time the EXPORTER ran, and
the UI compared it to the wall clock. It therefore measured the exporter's
liveness and displayed it as the data's freshness. Measured with the real
objects: a candle **4,261 minutes old** was reported as **"0.0 s"** old.

The same confusion appeared in the health beat: `data_fresh` was
`bool(latest_prices())`, which reads the STORE and stays true for a store that
has not updated in months — which is why the failed session logged
`status: ok, data_fresh: true, errors: 0` all day while fetching nothing and
trading four positions on stale bars.

**Decision:** freshness is measured on the CANDLES, per symbol, against the bar
the exchange should have completed by now (`algo/trading/freshness.py`), and in
**bars rather than seconds** — one bar behind right after a close is the normal
fetch window, three bars behind at the same moment is a fault. Four states are
kept distinct, and the two that were conflated are now separate: `FRESH`,
`STALE` (market open, behind — shown WITH the reason), `MARKET CLOSED` (no bar
is due, so old candles are correct) and `MISSING`. `marks()` returns price and
bar time together so the two cannot be separated by accident, and `data_fresh`
now reports the fetch.

A second instance of the same class: `feed.freshness()` initially read the WALL
clock rather than the engine's `MarketClock`, giving the assessment its own
notion of the session. It was caught by its own test — no injected moment could
produce a STALE verdict. The clock is the single definition of market time.

### Minimum trade allocation (§5)

`min_trade_allocation` (default 25% of `deploy_today`, configurable). A
position that cannot reach the floor is **SKIPPED, never padded**: the floor is
a policy about which trades are worth taking, not a licence to breach the
per-trade cap, the capital available, or the portfolio risk budget. Sizing up
to satisfy a minimum would let the softest constraint override the hardest one.

The non-obvious consequence, now surfaced in preflight and in the wizard: a
floor of F caps CONCURRENCY at floor(1/F) regardless of `max_open_positions` —
a 25% floor with `max_open_positions=5` can only ever reach 4.

### Timeframes: strategies declare, config may only restrict (§3)

The scheduler already derived timeframes from the strategies, but
`config.timeframes` was a second hand-maintained list and several call sites
read `config.timeframes[0]`, so the data fetched and the data scanned could
diverge. `effective_timeframes(declared)` makes the strategies authoritative;
config may now only RESTRICT, never extend, because asking for a timeframe no
strategy trades would download data nothing reads.

Also: the live path seeded an uncovered symbol with the ingestion default of
**365 days**. Right for a history download, badly wrong for a live top-up —
adding one uncovered timeframe would have pulled a year per symbol at the first
refresh. Live now uses `live_lookback_days` (default 5).

### Single runtime state (§11)

Two genuine duplications were found and removed: `open_risk` was recomputed in
the exporter with the formula the risk engine already owns, and
`portfolio_value` meant the day's static allowance in one panel and the live
mark in another — the same name for two different quantities, which is exactly
how a panel starts contradicting the engine. `PortfolioRisk.portfolio_value` is
now the one definition; the allowance survives as `capital_base`.

### Tiered refresh (§6)

Prices and P&L change every second; the scanner, signals, explainability,
timeline and logs cannot change between completed candles. The export is split
into a fast tier (`live.json`, <1 KB) and the slow set, and position CARDS are
rebuilt only when their structure changes while the numbers inside are patched
in place — rebuilding every second closed any open panel and made the page
flicker. `refresh_live()` is bounded by `max_open_positions`, not by universe
size, so a 1000-symbol universe costs the same there as a 10-symbol one.

Live last-traded prices are used for **display and P&L only**; every decision
still reads completed bars, exactly as the strategies were measured.

### Verified, not changed

- **Scanner (§2):** already correct — a bar is evaluated once, the scanner
  advances to newer bars immediately and never regresses, and only the newest
  bar can fire. Traced end to end for one symbol.
- **Exit engine (§8) and square-off (§9):** all 12 intraday strategies verified
  through the real `TradeManager` for stop, stop-first precedence, honest gap
  fill, target, partial, breakeven, trailing, ratchet-only and square-off.
  Square-off holds even when the symbol has NO market data.
- **Strategy participation (§14):** 12 of 35 scan, and that is the design —
  the other 23 are SWING specs whose horizons run to 126 days; an engine that
  squares off at 15:15 would close them on entry day, every day.
- **Universe (§13):** the tiered, ADTV-ranked architecture already exists
  (dev 100 / paper 500 / production 1000). The 99 symbols are a DEVELOPMENT
  CONFIGURATION — `config.universe` defaults to empty, so the explicit
  `nifty100.txt` is used. The binding constraint is DATA, not code: of 745
  candidates, only 99 have 15m history (5m has 335). Widening the universe is a
  download, not a change.

**Scope:** no strategy logic, sizing rule beyond the declared minimum, or
execution path was altered.

---

## D-038 — First paper session: three defects, one authoritative instrument map, and an absence of evidence read as evidence of absence (2026-07-20)

The first live paper session logged `no instrument token for <symbol> -
skipping` for **all 99** watchlist symbols while the portfolio simultaneously
held open positions in several of them (HDFCBANK, RELIANCE, …), reported
`rows_fetched=0 rows_added=0` on every cycle, and failed to export `logs.json`
with `Access denied: logs.tmp -> logs.json`. Three defects, two of which shared
one root cause.

### 1. The instrument master was never loaded (root cause of defects 1 and 3)

`SmartApiInstruments` loads lazily, but `token_for()` had **no lazy trigger**.
`_lookup()` opened with `if self._master is not None:` and, when the master had
never been loaded, fell through and returned `None` — **indistinguishable from
"this symbol is not listed."** Loading was every caller's responsibility, and
five entry points each remembered it separately:

| caller | calls `ensure()` |
|---|---|
| `scripts/download_history.py:77` | yes |
| `scripts/build_universe.py:84` | yes |
| `scripts/run_paper.py:104` | yes |
| `AngelOneBroker.connect()` | yes — **live mode only** |
| **`scripts/run_trading.py`** (the production runner) | **no** |

In paper mode `build_adapter` returns `PaperBroker`, so the one in-engine
`ensure()` never runs. Every lookup therefore failed **while the parsed master
sat on disk unread** (`user_data/data/nse/_instruments/smartapi_nse_eq.parquet`,
2,406 rows, written 2026-07-17). Nothing was broken but the missing call:
resolving the real 99-symbol watchlist against that cache now yields **99/99,
with zero network calls**.

The portfolio/provider "disagreement" was therefore not a disagreement about
symbol identity at all — the engine's positions came from stored candles, and
the provider simply had no map loaded to answer with.

**Decision:** the map loads **itself**. `_lookup` ensures the master on first
use (cache-first, network only if absent), exactly as the index path already
did. Callers cannot forget because it is no longer their job. Lookups are
O(1) against a normalized dict, a failed load is remembered (99 symbols × every
cycle must not become 99 downloads) and clearable via `reset()`, and
`-EQ`/case/whitespace forms round-trip so the broker's own tradingsymbol
resolves. Other NSE series (`-BE`, `-SM`) are deliberately NOT folded onto the
EQ scrip — they are different instruments with different tokens.

### 2. `rows_fetched=0` could mean two opposite things

An unmappable symbol returned an empty frame, which `IngestionEngine` recorded
as `empty` — the same status a genuinely quiet market produces. **A total
mapping failure was therefore reported in exactly the vocabulary of a normal
closed-market cycle.** The engine then compounded it: `data_fresh` was computed
as `bool(latest_prices())`, which reads the **store**, so it stayed `True` for a
store that had not been updated all day. The session's own health beats say
`status: ok, data_fresh: true, errors: 0` while nothing was being fetched — and
**four positions were opened and closed on stale bars** under that banner.

**Decision:** providers may declare `unavailable_reason(symbol, timeframe)`
(optional; default `None`), checked BEFORE fetching. Statuses now separate
`NO_NEW_DATA` (`up_to_date`, `empty`) from `CANNOT_FETCH` (`unavailable`,
`quarantined`), `IngestionReport.diagnosis()` states which in one line with the
reason and examples, and `data_fresh` reports the **fetch**, not the store.
An absence of rows is never again allowed to stand in for an absence of faults.

### 3. `os.replace` loses to the dashboard's own file server on Windows

`os.replace` is atomic on Windows but raises `PermissionError [WinError 5]`
while another process holds the **destination** open — Python's `open()` does
not pass `FILE_SHARE_DELETE`, so an ordinary reader blocks it. The reader was
the dashboard's own static server: the browser polls all ten snapshots every 2 s
(`POLL_MS = 2000`), and `logs.json` — the largest payload — is held open longest,
which is why it failed **by name**. A single `try/except` around the whole
export meant one locked file aborted the rest; `logs.json` being written last
was luck, not design.

**Decision:** keep the atomic `os.replace` and retry it briefly (5 attempts from
10 ms, ~150 ms worst case) — the collision lasts one file send. If every attempt
loses, the write is **skipped, not raised**: each snapshot is a complete picture,
so the next export restores it. Staging files are per-PID, swept at startup, and
removed on failure; each snapshot is built and written independently; and a file
that keeps losing surfaces on `health.json` rather than only in the log.

### Diagnostics (defect 4)

99 identical warnings per cycle became **one** line — `15m: UNABLE TO FETCH
candles for 99/99 symbols - no instrument token in the NSE master (examples:
ADANIENT, ADANIPORTS, APOLLOHOSP, ASIANPAINT, AXISBANK, +94 more)` — emitted as
a `data` event and re-stated only when the situation **changes**, so a standing
fault does not scroll a session. The dashboard gains `TOKEN MAPPING 97 / 99
resolved` and a `MARKET DATA` state (`OK` / `UNABLE TO FETCH` / `OFFLINE`), both
sourced from the same authoritative map the provider and broker resolve through,
so the panel cannot drift from what trading sees. Preflight now verifies mapping
at startup: a total failure is **critical** (no data can arrive), a partial one
is a warning.

### Collateral finding — the test suite overwrote a live session's snapshots

`TradingConfig.dashboard_dir` defaults to the real `dashboard/dashboard_data`,
and three tests built a `ProductionEngine` without overriding it. Running the
suite during this work overwrote the snapshots of the actual paper session (the
authoritative record — `events-*.jsonl`, `portfolio.json`, the daily summary —
was untouched; those files are derived presentation state). Fixed at the call
sites, plus an autouse conftest guard that **content-hashes** the live folder
around every test and fails any test that writes into it. Content hashing, not
mtimes: NTFS granularity is coarse enough that a fast rewrite can reuse an
mtime — a flaw the guard's own test caught.

**Scope:** no strategy, sizing, risk or execution logic was modified. Every
change is in the data/instrument layer, diagnostics, or presentation. 655 tests
pass (up from 620); 35 new.

---

## D-037 — Phase 17: fidelity mode built; the "chase" is mostly the price of information; intraday track closes (2026-07-18)

Built the Fidelity Evaluation Mode (`research/fidelity.py` — isolated research
path; zero production imports): declarative `ExecutionSpec` (trigger/close/
next-open entries, structural stops, fixed-R/level targets, one partial with
breakeven, optional ATR trail, EOD), honest gap-through fills on stops AND
targets, stop-before-target same-bar convention, 11 semantics tests. The five
published specs come from the D-036 audit, fixed, untuned.

**Part D (99 symbols, matched signals):** under published execution the
trigger-entry strategies swing hugely positive — orb −12.3→+19.7 net bps (PF
1.84), cpr −14.0→+13.2 (PF 1.96), first_pullback −9.7→+6.8; vwap_pullback
−11.4→+2.3; vwap_15m unchanged (−12.4→−12.9 — its published entry IS
close-based: the internal control). **Part E ladder:** d_entry = +17..+35 bps is
essentially the whole swing; published stops (d_stop −1.5..+0.2) and targets
(d_target −1.8..−0.5) contribute ≈ NOTHING — D-006's exits-don't-create-edge,
fourth reproduction.

**The decisive control:** trigger-mode fills condition on the bar CLOSING beyond
the level — information that does not exist at the fill moment. The ORB
TOUCH-basis run (a real resting order: every first touch, failures included)
collapses to **−10.2 net bps** vs +21.0 close-confirmed. So the D-036 "chase" is
not recoverable dead-weight — it is mostly **the price of knowing the close**,
and the production close-of-bar baseline is the honest implementable
measurement.

**Part F:** orb / vwap_pullback / vwap_15m — class 1 (published form lacks edge
on this corpus, realizably executed); cpr / first_pullback — class 3
(inconclusive: positive only at the unimplementable bound). ALL remain archived;
none becomes a production candidate; **no AI-guided refinement** (it would
optimise toward foresight). The intraday production track closes: cost wall
(D-026) + no realizable published edge. Next research move: registry top item
R-002 (portfolio-mode factor evaluation). 387 tests pass.

## D-036 — Phase 16: baseline fidelity audit — we test our execution model, not the published systems (2026-07-18)

Audit of the five Phase-15 intraday baselines (`research/BASELINE_FIDELITY_AUDIT.md`;
no code modified; execution semantics PINNED by 6 new characterization tests).

**Answer to the audit question:** the entry CONDITIONS are faithful, but the
published SYSTEMS are materially altered by four framework-wide substitutions —
(1) long-only, (2) entry at the signal candle's CLOSE instead of at the trigger
level, (3) NO profit targets, (4) wide ATR/structure stops instead of published
tight structural stops. All four are intentional frozen policy (D-006/D-023),
but the Phase-15 result must be read as "our platform trading these entries",
not "the published strategies fail on NSE". **Zero category-1 (faithful)
verdicts; all five are category 2** (material differences to account for before
further research); vwap_pullback additionally over-fires 5–9× (our
too-permissive encoding of a discretionary setup).

**Measured attribution (40-symbol decomposition), ranked:** (1) COSTS — gross
expectancy is ~zero for all five (−1.6..+3.1 bps), net = gross − 12.2: the loss
IS the cost stack on zero-edge fills (L-006 again). (2) ENTRY TIMING — the
close-of-bar fill pays a measured **+14..+44 bps chase** above the published
trigger; the largest recoverable component, bigger than the entire cost stack
for 3 of 5 (upper bound: touch-fills include failures). (3) EXITS — 83–91% of
trades ride to square-off at −2..−7 bps (modal trade = costed noise round-trip);
reshapes the distribution though crypto evidence says exits don't create mean
edge. (4) signal/day-type gates absent; (5) universe fine. Execution-model flags:
at-stop gap fills are OPTIMISTIC (no gap-through); cost model verified vs Angel
One current — essentially exact, one outdated sub-rate (NSE txn 0.00297% vs
0.0030699%, ~0.2% of cost, optimistic), unchanged per instruction.

**Decision/recommendation:** do NOT proceed to selectivity filters or AI-guided
refinement against this baseline — that would optimise against the chase
artifact. The correction path (owner decision) is a **fidelity evaluation mode**
(trigger-level entry approximation + published stop/target emulation as a
measurement variant), leaving frozen engines untouched. Baselines remain frozen.

## D-035 — Phase 14: R-001 executed — short-horizon per-trade effects do not certify; expiry is the best-ever lead (2026-07-18)

First execution under the research governance process (D-034). Pre-registered
(`research/PREREGISTRATION_R001.md`) THREE independent short-horizon families —
quality over quantity, no parameter sweep — implemented via the frozen hypothesis
framework (entry logic local to `scripts/research_r001.py`; the frozen
components/compiler/gate untouched) and measured on NIFTY-500. Multiple-testing
context reported: 3 tested this phase, 30 lifetime.

**Results (all FAIL under the frozen gate), by family (Part D):**
- **F3 expiry (F&O expiry-week drift): INCONCLUSIVE — directionally supported,
  the BEST short-horizon result in the project.** Selection +74.4 bps point
  (clears the 30.9 cost), beats random (excess-vs-random +0.53%, rel-PF 1.35,
  54,000 signals) — but CI-low **−1.6 bps**, so it fails to establish even a
  positive selection edge at 95% (tighter than tom's −7.9, but still short of the
  0 line, let alone cost). Not rejected, not certified.
- **F2 gap-fade: REJECTED / FALSIFIED** (selection −45 / CI-low −167). NSE
  down-gaps CONTINUE, they do NOT fade. With egap (up-gaps do not continue up),
  the gap evidence is: downward continuation, no mean-reversion.
- **F1 month-start: REJECTED** (+18 / −21). The turn-of-month edge lives in the
  pre-month-END anticipation (tom), not the month-START inflow arrival.

**The load-bearing finding:** even at MAXIMUM available power (54k signals, a
3-7-day horizon — the most powered configuration the project can construct), a
per-trade short-horizon selection edge does not certify. The pre-registered
short-horizon prediction is REFUTED for certification. This is not a power
failure to fix with more data — it is the drift/noise floor of per-trade equity
selection on NSE.

**Recommendation (Part E): ARCHIVE R-001, proceed to R-002 (portfolio-mode).**
Justification: (1) the short-horizon per-trade thesis is answered — the best case
(expiry, max power) fails; (2) the directionally-real expiry effect's natural
next test is PORTFOLIO mode (a monthly expiry-window basket → ~130 near-
independent monthly returns may certify where the per-trade CI cannot), which is
R-002 — the registry's top item; (3) continuing R-001 would mean tweaking expiry
parameters to chase the CI, which is forbidden. Not "pause for data" — R-001
needed none; R-002 needs none either (only owner sign-off on the portfolio-CI
evaluation mode). Expiry is carried into R-002 as its priority test case.

No promotion (nothing certified); paper engine off. Evidence persisted
(r001_expiry/gap_fade/month_start; report `r001_league.md`). 351 tests pass.

## D-034 — Phase 13: permanent research governance (registry, quality framework, standards) (2026-07-18)

The research platform and validation framework are stable and frozen (no changes
to VALIDATION_RULES, D-031, the gate, methodology, the hypothesis compiler, the
components, the framework, or the research engine). This phase made the research
PROCESS permanent policy — no code, no new strategies, no optimisation.

**Delivered:**
- **`docs/RESEARCH_REGISTRY.md`** (Parts A/C/D): the single source of truth for
  every research idea. No idea is implemented before it exists here. 11 active
  entries (R-001..R-011) for the unexplored directions from the post-mortem, each
  with the full schema (rationale, source of edge, measurable prediction,
  falsification criteria, expected power, dependencies, status), plus an ARCHIVED
  map of the 27 explored/rejected strategies by family so nothing is re-proposed.
  Ranked backlog by the quality score.
- **`docs/RESEARCH_STANDARDS.md`** (Parts B/E): permanent policy — pre-
  registration before implementation, measurable/falsifiable hypotheses, the
  frozen gate as the only success criterion, reproducibility, documentation, the
  promotion workflow, and mandatory archival of negative results. Plus the
  Hypothesis Quality Framework: a 7-dimension weighted rubric that PRIORITISES
  research order (weighting expected statistical POWER ×3 and INDEPENDENCE from
  rejected ideas ×2.5 — the project's two hardest lessons). It ranks order, never
  predicts a PASS.

**Prioritised backlog:** R-001 (short-horizon effects, 4.24, zero new infra) and
R-002 (portfolio-mode factor evaluation, 4.28, needs a new evaluation MODE — owner
decision) tie at the top; then R-003 earnings/PEAD, R-004 index flows, R-010
sector-relative. Abandoned families are archived with evidence.

**Readiness assessment (Part F): the project IS ready for long-term systematic
research.** The governance loop is complete and closed: idea → registry →
prioritise (quality framework) → pre-register → compile (framework) → measure
(frozen gate) → archive (registry). Residual PROCESS weaknesses, none requiring
major new infrastructure:
1. **Multiple-testing discipline must be enforced** as screening throughput rises
   (the registry count is the denominator; a single PASS is provisional pending
   pre-registered out-of-sample confirmation). Covered by STANDARDS §3; must be
   applied, not just written.
2. **No out-of-sample holdout is currently reserved** — all 11 years were used for
   measurement. Any future PASS must be confirmed on held-out/walk-forward data
   (VALIDATION_RULES §10, STANDARDS §1.6) before promotion. Reserve a holdout
   before the first screening batch.
3. **R-002 (portfolio-evaluation mode) is the one genuinely-required NEW
   capability** — and only if the owner wants to pursue certifying the
   directionally-real factors. Owner-gated, registered, not built.
The frozen gate's low long-horizon power is a DATA property, not a process
weakness. No other new infrastructure is required.

## D-033 — Phase 12: research post-mortem + pivot to automated hypothesis research (2026-07-18)

27 strategies measured, 0 certified — treated as a completed research RESULT, not
a failure. The validation framework is final (frozen: VALIDATION_RULES, D-031,
the gate, CIs, baselines, benchmark logic — none may change). This phase reviewed
the PROCESS and redirected it (`research/POSTMORTEM.md`).

**Post-mortem (A).** By family: cross-sectional RANK signals (momentum, RS,
liquidity, beta, stage/breakout) are DIRECTIONALLY real (large consistent
positive point edges) but uncertifiable at 21-126d horizons; per-symbol
LEVEL/own-trend signals carry no selection edge; intraday is cost-dead; the
low-volatility anomaly is INVERTED on 2015-2026 NSE; confidence heuristics carry
no signal (4th time). No family is "exhausted as a phenomenon" — several are
directionally real — but all are exhausted as LONG-HORIZON per-trade tradeables
under the gate's power. tom (calendar, 3-7d, CI-low −7.9) is the lone
near-certification and the signpost: short horizons + many near-independent
events are where the gate has power.

**Knowledge gaps (B).** Untested, higher-value frontier: short-horizon event/
microstructure, true event-driven (earnings/index-change — needs data),
portfolio-level factor evaluation, multi-factor ensembles, regime-conditional
allocation, inter-market signals, adaptive/walk-forward selection.

**Research-engine evolution (C) — IMPLEMENTED.** `algo/research/hypothesis.py` +
`components.py`: a hypothesis is now DECLARED from reusable metrics/filters/
entries and `compile_hypothesis` turns it into a real `StrategyProfile` on the
existing seam — the frozen gate/measurement untouched. Equivalence-tested
(compiled momentum == hand-written xsmom, bit-identical; compiled breakout ==
donchian). Compiled hypotheses are `enabled=False` research objects (never
live-discovered), so no tradeable strategy or promotion path is added. Grids
become loops (`compile_all`). This is the requested faster research process; no
methodology change.

**Composition (D) — DESIGN ONLY.** Rank aggregation of orthogonal directional
signals (fixed pre-registered weights); weighted voting; and — the strongest lead
— portfolio-level evaluation (a monthly-rebalanced decile's ~130 near-independent
monthly returns may certify where the per-trade view cannot; a new measurement
MODE, not a gate change). None implemented.

**Roadmap (E)** in `IMPLEMENTATION_ROADMAP.md` §0: ranked #1 short-horizon
effects → #2 portfolio-mode factor evaluation → #3 event data → #4 ensembles →
#5 throughput. Abandon: more per-symbol technicals, low-vol, long-horizon single
sorts.

**Final assessment (F).** The framework is sufficient for JUDGING (correctly
sized, reproducible, exercised on 27); its low long-horizon power is a DATA
property, not a bug. More history is a marginal lever (11y ≈ SmartAPI limit);
choosing POWERED horizons + acquiring EVENT data are the real levers. **The
project is ready to transition from manual strategy implementation to automated
hypothesis generation + screening** at powered horizons under the frozen gate.
351 tests pass (+7 framework). Nothing promotable; paper engine off.

## D-032 — Phase 11: NIFTY-500 × 11-year corpus, the cross-sectional seam, batch 2 (2026-07-18)

The measurement methodology is frozen (D-031 is final). Phase 11 attacks the
POWER problem L-011 identified by strengthening the DATA and adding the strategy
class the frozen gate was always meant to judge.

**Corpus (Part A).** Expanded the daily research corpus from 99 NIFTY-100 symbols
× 3.5y to the full **500 NIFTY-500 symbols × up to 11.5y** (2015-01-01 →
2026-07-16), reusing the D-029 head-gap backfill wholesale: 401 new symbols
downloaded, 97 existing backfilled to 2015, 0 quarantined, 0 excluded (all 500
map to live tokens). Median history 2857 bars (~11.3y); 33 short symbols are
genuine recent IPOs (MEESHO, GROWW, LENSKART…), 193 listed after 2015 — detected
and reported, not hidden. The NIFTY-500 membership is the committed, dated NSE
snapshot (`user_data/universe/ind_nifty500_2026-07.csv`), intersected with the
live master — real exchange data, not fabricated, same discipline as the
NIFTY-100 snapshot. Intraday (1h/15m) deliberately left at NIFTY-100/3.5y: batch
2 is entirely daily, and 10y×500 intraday would be a 2.5-hour download of zero
batch-2 value. **Power gain: independent 20-day blocks/symbol ~44 → ~143 (×3.2),
usable breadth 99 → 467 (×4.7).** The scarce axis (independent time periods) is
up ~3.2×.

**Cross-sectional seam (enabling infra, additive — NOT a redesign).** The
prioritised batch-2 families (cross-sectional momentum, residual momentum, low
vol, beta, reversal, rank forms) judge a stock by its RANK among peers each day,
which the per-symbol `entry_signal` cannot express. Added ONE optional hook,
`StrategyProfile.prepare_cross_section(frames)` (default no-op), which the engine
calls once per sweep with all prepared frames before computing signals;
`prepare_signals` branches on `cross_sectional` so the per-symbol path (all of
batch 1, the six incumbents) stays bit-for-bit identical. Reusable primitives
(`strategies/cross_section.py`): decile flag, composite percentile, equal-weight
market return, breadth, edge-trigger, and a DRY `CrossSectionalDecileStrategy`
base. The measurement and the frozen D-031 gate are UNTOUCHED — a cross-sectional
strategy is judged by the same selection-edge-vs-random test as everything else;
the random baseline (random stock, random time) is exactly the right null for
"did your ranking pick better than a coin flip." Lookahead-safety proven by a
truncation test (a date's rank uses only that date's values).

**Batch 2 (Parts B-E).** Removed six roadmap candidates that merely
re-parameterise rejected batch-1 hypotheses (200-DMA trend, VCP, NR7, Darvas/
Turtle, Elder pullback, and D3 which IS hvol). Pre-registered
(`research/PREREGISTRATION_BATCH2.md`) and implemented 13 strategies spanning
every requested family — 11 cross-sectional (xsmom, resmom, dualmom, lowvol, bab,
xsrev, maxret, hi52rank, illiq, combo, breadth-regime) + 2 per-symbol (tom
calendar, stage2 Weinstein). The RANK forms of momentum and 52-week-high are
kept as genuinely different mechanisms from the rejected per-symbol/threshold
forms; nothing is a tuned variant of a dead idea.

**Measurement (Parts F-H): all 13 FAIL — but the pattern is new and it is not
"no effect".** Measured on NIFTY-500 under the frozen D-031 gate (report:
`user_data/backtest_results/reports/batch2_nifty500_league.md`; NIFTY-100
cross-checked — same pattern, configurable via `--symbols-file`).

* **Selection POINT edges are large, positive and CONSISTENT** — illiq +392,
  stage2 +383, hi52rank +252, bab +232, breadth +191, xsmom/dualmom +149,
  resmom +112, tom +75 bps (only lowvol negative, −29). 11 of 13 also BEAT the
  random baseline at the managed level (excess-vs-random > 0, relative PF > 1;
  bab PF 1.79× random). Batch 1's per-symbol signals had no consistent
  direction; **batch 2's cross-sectional signals do — the factor effects are
  DIRECTIONALLY present on NSE, matching the literature's sign.**
* **But every selection CI lower bound is deeply negative** (−8 to −1070 bps),
  so none is statistically established. At 60-126-day horizons even 11 years ×
  500 names cannot resolve a ~150 bps edge from zero: the independent
  long-horizon periods are irreducibly few and per-period variance is huge
  (2015-2026 spans COVID). The non-certification is a POWER statement, not a
  "no edge" statement.
* **Every strategy loses to buy & hold** (excess-vs-B&H negative for all 13):
  long-only, holding a decile for weeks, cannot beat holding the whole universe
  through an 11-year bull. Long-only truncation of the short leg, as
  pre-registered.
* **The standout: tom_daily** (turn-of-month) — selection +75 bps, CI-low
  −7.9 bps: the CLOSEST any strategy across both batches has come to
  certification, because it is SHORT-horizon (3-7 days) with a HUGE sample
  (53,645 signals). Short horizon + many signals = the tight CI the long-horizon
  factors cannot get. This localises where the frozen gate has power.

**Statuses: all 13 -> `rejected`** (new evaluation generation; incumbents
untouched; 27 strategies on record). **Nothing is promotable; the paper engine
stays off. Do not chase these; do not weaken the gate.**

**Meta-analysis (G) & recommendation (H).** Consistently rejected: every family,
at multi-week/month horizons. Showing promise (directionally, not certifiably):
cross-sectional momentum, 52-week-high rank, liquidity and stage effects — large
consistent positive point edges. Recurring pattern: big point edge + enormous CI
+ sub-B&H return. Abandon: long-horizon single-factor sorts on one 11-year market
— the CI is unpowerable there by construction, and lowvol's negative point edge
shows the low-vol anomaly is inverted on this bull sample. Deserves exploration:
SHORT-horizon high-frequency effects where the sample certifies (tom is the
proof-of-concept). **Single highest-value next step (no methodology change):
shift research to short-horizon, high-frequency effects following the tom_daily
lead — where 500 symbols × 11 years yields the tens of thousands of near-
independent observations the frozen gate needs — NOT another batch of multi-week
factor sorts whose CIs this data cannot close.** No new strategy will pass a gate
the data itself cannot power at long horizons.

## D-031 — Benchmark amendment: the gate is now selection edge over random, and it FAILs all 14 (2026-07-17)

Phase 10 amended the frozen protocol (VALIDATION_RULES §26, owner-approved) to
close the L-010 defect: the promotion gate is no longer absolute return but the
**selection edge** — forward return in EXCESS of a random entry drawn from the
same corpus — with a D-028 block-bootstrap CI on the *difference*. Promotion
requires the selection-edge CI lower bound to clear the round-trip cost, exactly
as D-007 required of the absolute edge; the absolute §7 bars are retained
(PASS needs both). A benchmark battery (buy & hold matched-per-trade + portfolio;
random entry, 3 seeds, matched count; random matched-holding) is computed for
every daily strategy and reported with comparative metrics (excess vs B&H,
excess vs random, information ratio, relative PF/DD). All deterministic, all
priced through the same risk engine via one shared `simulate_entries`.

**Re-judgement of the whole database (Part C), no parameter/horizon/cost change:
ALL 14 strategies FAIL, 5 status changes** (wyckoff_spring, hvol, triple_screen
`measured`→`rejected`; donchian55, tsmom `draft`→`rejected`; the other 9 already
rejected). The finding that matters:

* The batch-1 "winners" have positive selection POINT estimates — wyckoff +64.0,
  hvol +109.5, triple_screen +23.5 bps — but their difference-CI lower bounds
  are deeply negative (−108.5 / −82.3 / −128.9 bps). **The apparent edge is not
  statistically distinguishable from a random entry.** At 20–60-day horizons on
  3.5 years, both the strategy and the random baseline carry huge between-period
  drift variance, and ~14–40 independent blocks cannot resolve a ~60 bps edge
  from zero. This is the pre-registered sample-size warning (roadmap §2) made
  rigorous: a long-horizon edge cannot be *established* on this data, whatever
  its point estimate.
* The L-010 diagnostic's +67/+91 bps were POINT estimates; D-031 shows they are
  within noise. Nothing is disproven — it is *not established*, which for a
  deployment gate is the same verdict.

**Validation (Part D):** the drift-control regression test (a strategy entering
arbitrarily in a rising market) now FAILs for lack of selection edge; a
planted-selection control PASSes (the gate credits genuine skill when the sample
supports it); the intraday incumbents reproduce D-026 to the decimal (volexp
9.13, orb 3.01, vwap 2.83, pullback 0.17 bps); the daily incumbents correctly
reflect the D-029 RELIANCE/TCS repair (ema200 1402→1420 signals). Determinism is
unit-tested (seeded bootstraps + benchmark battery). 285 tests pass (+8).

**Known limitation, stated not hidden (Part E):** the two-sample difference CI
treats the strategy and random samples as independent, so it does NOT cancel the
common market-drift variance — it is correctly SIZED (drift cannot pass) but has
low POWER on short samples. A *paired* date-matched cross-sectional selection
edge (strategy return minus the same-day universe mean) would cancel drift
per-observation and detect a real cross-sectional edge with far more power. That
is the recommended next amendment — deferred to owner approval rather than
adopted after seeing it changes outcomes, which would be methodology-shopping.
The current gate is adopted as pre-registered (D-030), and its honest result
stands: no strategy is deployable on this evidence.

## D-030 — Batch 1 measured: 3 PASS / 2 BORDERLINE / 3 FAIL — and the control that voids deployment (2026-07-17)

Phase 9 implemented the eight pre-registered candidates
(research/PREREGISTRATION_BATCH1.md) and measured them on the real 99-symbol
corpus through the unchanged pipeline. Frozen-rule verdicts, persisted as
statuses per the existing promotion map:

* **PASS → `measured`**: wyckoff_spring_daily (PF 1.44, +0.58% expectancy,
  n=3,772), hvol_daily (PF 1.29, n=1,522), triple_screen_daily (PF 1.29,
  n=3,679).
* **BORDERLINE → `draft`**: donchian55_daily (PF 1.16), tsmom_daily (entry-edge
  leg passed while managed trades LOST — PF 0.80, −0.31% expectancy).
* **FAIL → `rejected`**: hi52_daily, squeeze_daily, egap_daily.

**But the due-diligence control (L-010) voids deployment**: 2 of 3 seeded
RANDOM-entry strategies also PASS the frozen bars on this corpus — at 40–60-day
horizons the absolute-return gate is saturated by bull-market drift plus the
survivorship of measuring today's constituents. Decisions taken:

1. **Statuses stand as the frozen rules produced them** — moving them by a
   test invented after seeing results would be goalpost-moving in the other
   direction. But they are explicitly NOT deployment evidence.
2. **The paper engine must NOT be started** although `measured` statuses now
   exist that would let it. No paper trading until the gate is amended.
3. **Proposed amendment (owner approval required — D-003 froze the protocol):**
   add a drift-adjusted leg to D-007 — the SELECTION edge (gross minus the
   random-entry baseline) must clear the round-trip cost on its CI lower
   bound — and run seeded random-entry controls alongside every real
   measurement, reported in the league table. Under that lens batch 1 reads:
   hvol +91 bps and wyckoff_spring +67 bps selection edge at 20d (both clear
   30.9 bps; genuinely interesting), triple_screen +22 (does not),
   donchian ≈ 0, everything else negative.
4. **No re-tuning of anything measured** — batch-1 parameters stay frozen as
   pre-registered; the amendment re-JUDGES recorded measurements, it does not
   re-cut strategies.

Reproducibility verified: re-running a batch strategy records 0 new signals and
reproduces its verdict to the decimal; the six incumbents' statuses and numbers
are untouched; discovery picked up all eight modules with zero registration
edits. 277 tests pass (37 new).

## D-029 — RELIANCE/TCS truncation: forward-only incremental update; head-gap backfill added (2026-07-17)

**Root cause (evidenced, not guessed).** The two symbols' daily history began at
exactly 2025-01-01 (381 bars) while 96 peers held 877 bars from 2023-01-01 — on
the 1d timeframe ONLY (their 1h/15m were complete). File mtimes show
RELIANCE/TCS 1d written at 15:57:37 as a pair, 14s BEFORE the alphabetical
99-symbol batch began (15:57:51), and never touched again; their 1h/15m files
were written in-sequence inside the main batch. So a separate immediately-prior
invocation — a pre-flight smoke test on the two docstring example symbols,
1d-only, started at 2025-01-01 — seeded the store, and then the code defect took
over: ``IngestionEngine.incremental_update`` computed only the FORWARD window
(``last_date + step → end``) and never compared stored coverage START against
the requested start. The full download reported the symbols ``up_to_date`` —
true of the tail, silently false of the requested window. Classification:
download pipeline behaviour (not SmartAPI, not corporate actions, not mapping,
not caching). The 16:27 mtime cluster separately confirms the D-025 quarantine
recovery (SBILIFE/BANKBARODA/IRCTC/PNB/RECLTD/TORNTPHARM).

**Fix.** ``incremental_update`` now also fills the HEAD gap when a requested
start is given (loud ``backfilled`` status; same quality gates; store upsert
merges). Callers that pass no start — the paper engine's rolling top-up — keep
tail-only behaviour, so no surprise multi-year download can occur mid-session.
Repaired live: both symbols now 877 rows from 2023-01-01; re-run is a no-op;
store audit clean (only JIOFIN starts late — genuine, listed 2023-08-21).
5 new tests. NOTE: the D-026 verdicts were measured on the truncated store;
the next real measurement will see RELIANCE/TCS's full daily history (a ~2%
signal-count shift on daily strategies). Statuses on record are unchanged.

## D-028 — Long-horizon CIs use the stationary block bootstrap; verdicts preserved, gate honestly harder (2026-07-17)

The edge lab's day-clustered CI (L-009) is correct only while a signal's
forward window fits inside one day. At an 8-day horizon, signals days apart
share most of their window — the day resample treats them as independent, so
the CI is too NARROW and the D-007 gate too EASY. ``edge_lab`` now resamples
contiguous blocks of days per horizon (block = days the forward window spans:
``ceil(bars / bars_per_day)``, from ``MarketConfig.minutes_per_session``),
REUSING ``validation/monte_carlo.py``'s existing stationary bootstrap (Politis
& Romano) — one resampling implementation in the project. Horizons inside one
day keep the original day resample bit-for-bit; direct ``edge_lab.measure``
callers are unchanged unless they opt in; the engine always opts in. The block
length is recorded per horizon (``ci_block_days``) for audit.

**Verified on the real 99-symbol corpus:** all six verdicts identical (FAIL);
15m CI-lows bit-identical (0.26/−1.95/0.05 bps); volexp_1h (block 2) unchanged
to 1dp; and the finding that justifies the change — **ema200_daily CI-low
23.4 → 6.2 bps, nr7_daily 4.0 → −13.0 bps**. The day bootstrap had been
overstating long-horizon confidence by ~17 bps; every prior long-horizon
narrative ("within 4% of the bar") holds for the POINT edge only. Promotion
rules, bars, and ``verdict_for`` untouched. A synthetic-overlap test proves the
widening direction; a planted-edge control still PASSes (discrimination
retained). Done BEFORE any long-horizon candidate exists, so the harder gate
cannot be mistaken for moved goalposts.

## D-027 — Research pipeline: one seam, pre-registered horizons, measured throughput (2026-07-17)

Phase 8 turned the measurement machinery into a research pipeline a new
candidate plugs into, **without changing a single verdict** (verified: the six
D-026 results reproduce exactly on the real 99-symbol corpus).

* **`ResearchEngine.research` / `research_all`** is now the single seam: register
  → record signals with confidence → label matured outcomes → measure entry edge
  → simulate → evaluate → cost sensitivity → calibration → verdict. The loop used
  to be hand-wired in `scripts/run_measurement.py`, so a second entry point would
  have had to duplicate it. `_judge` is the one implementation of the evaluation
  every candidate is judged by.
* **Strategies are discovered, not listed.** `algo.strategies.library` discovers
  its own modules through the existing `StrategyRegistry`, so adding a candidate
  is *one file* — the hand-maintained `ALL_STRATEGIES` tuple that measurement,
  paper trading and the tests all depended on is gone. Name uniqueness is now
  enforced at import (the registry raises) rather than by a test.
* **The measurement horizon is PRE-REGISTERED per strategy** (`meta.horizon_bars`,
  `meta.max_hold_bars`), defaulting to exactly the Phase-5/7 values (1/2/4/8 bars,
  8-bar hold) so every recorded verdict stays reproducible. This closes a real
  gap: the horizon was hardcoded platform-wide, so a candidate whose thesis needs
  weeks could only ever be measured over 8 bars. It is declared *with the
  hypothesis and versioned with it*, and **no CLI flag exposes it** — re-running a
  candidate at a horizon chosen after seeing its verdict is precisely the
  curve-fitting D-026 forbids, and a flag is how that would happen by accident.
* **Throughput, measured rather than assumed.** Profiling the real 15m corpus
  showed the suspected bottleneck (indicators recomputed 3x per strategy) was
  **0.4% of the run**; vectorized pandas is simply fast. The real cost was
  `record_signals` at **54%** — a transaction (a disk sync) and a SELECT *per
  signal*, over tens of thousands of signals. Reusing the logger's existing batch
  writer + a single duplicate-guard query cut it **14.77s → 0.60s (24x)** and the
  whole pipeline **2x** (27.5s → 13.4s per strategy per 5 symbols); a re-run is
  now 0.04s. This is D-025's labeling finding applied to the recording step it
  had left per-row. The single-pass `SignalSet` stays (it is correct and removes
  duplication) but is documented for what it is: ~3s per strategy, not the fix.
* **`--strategies` subset filter**: iterating on one new candidate no longer
  re-measures the five already on record. Identical pipeline, identical bars.

**Known limitation, deliberately not fixed** (it would change measurement, which
this phase froze): `label_outcomes` and `simulate_strategy` each run
`simulate_trade` over every signal — the same simulation computed twice, now ~85%
of the remaining runtime. They are not trivially unifiable: the labeler simulates
on raw store bars with a fixed ATR(14), while the simulator uses the strategy's
prepared frame and will honour a strategy-supplied `atr` column. That divergence
is worth resolving on its own evidence, not as a side effect of a throughput
change.

## D-026 — ALL SIX STRATEGIES REJECTED on real NSE data (2026-07-17)

First real measurement: 99 NIFTY-100 symbols, 2.85M bars (2023-01→2026-07),
125,794 signals, 125,719 labeled outcomes, no tuning. **Every strategy FAILS**
the pre-registered bars and is `rejected` in the evidence DB; the paper engine
refuses to start. Evidence:

* **Intraday (pullback_15m, vwap_15m, orb_15m, volexp_1h): decisively dead.**
  Gross edge 0.2–9.1 bps against a 12.2 bps round trip; PF 0.52–0.69; Sharpe
  −4 to −11.7; MFE/|MAE| 1.03–1.16 (near coin-flip). This is D-007/L-006
  reproduced on Indian equities: **cost, not signal quality, is the binding
  constraint at intraday frequency.**
* **Daily (ema200_daily, nr7_daily): real but insufficient edge.**
  ema200_daily has POSITIVE expectancy (+0.0025, PF 1.20) and a 59.4 bps point
  edge over a 30.9 bps delivery cost — but its 95% CI lower bound (23.4 bps)
  does not clear cost, and PF misses the 1.25 floor. The bar is the bound, not
  the point estimate (§14). It is the only candidate meriting further research.
* **Confidence carries no signal**: correlation with realized P&L −0.026 to
  +0.018 across all six — L-003 reproduced. The Phase-4 heuristic scores are
  worthless on this evidence and must be replaced by evidence-calibrated
  priors, never hand-tuned.

**Decision: do not deploy anything. Do not tune on this data** (it would be
fitting the only honest sample we have). The next research must attack the cost
hurdle structurally — lower frequency, larger moves, or a genuinely stronger
entry — not re-parameterize losers.

## D-025 — Live-data defects: rate limits, glitch bars, O(n²) labeling (2026-07-17)

Three real defects only a live run could expose, all fixed and tested:
(1) SmartAPI enforces its rate limit harder than documented and reports it as a
non-JSON body the SDK raises as a *parse* error — it looked like data
corruption and quarantined 47 symbol-timeframes. Now detected, backed off
exponentially, retried (1h 74→99, 15m 55→99).
(2) Rare impossible bars (~1 per 20,000) were discarding entire multi-year
histories. Now dropped individually — explicitly counted and logged, never
silently reshaped (L-008) — with tolerance `max(abs_floor, pct×rows)`; the
absolute floor exists because one bad bar in an 877-row daily series is 0.11%
while the identical defect in 15m data is 0.005% (1d 93→99). Systematically
broken feeds are still quarantined whole.
(3) Outcome labeling scanned the frame per signal (O(signals×bars)) and synced
per row; a date→index map + batched writes made the real run tractable.

## D-024 — Leverage relaxes the notional ceiling; risk stays equity-based (2026-07-17)

Buying power is resolved from the broker (SmartAPI ``rmsLimit``) or an
explicitly CONFIGURED allowance — never a hardcoded 5x or any x
(``BuyingPowerConfig``: cash | multiplier | broker, with a ``max_multiplier``
guard so a mis-parsed field cannot silently inflate risk). Crucially, buying
power raises the DEPLOYMENT ceiling only: risk-per-trade continues to size from
account EQUITY, because a stop-out loses equity and equity does not grow with
leverage. So leverage lets a risk-justified position be HELD, it never enlarges
the risk taken. Capital is otherwise fully deployable across the three slots
(``max_capital_deployed`` default 1.0) — idle capital only ever results from a
configured risk limit. Also removed a duplicate ``daily_risk_budget`` field
from SizingConfig (PortfolioConfig owns it; a test caught the dead copy).

## D-023 — Net break-even protection and selectable trailing modes (2026-07-17)

"Move the stop to entry" is a trap: exiting at entry loses the full round trip.
``risk/breakeven.py`` computes the true net break-even — entry grossed up by
brokerage + STT + exchange + GST + stamp + SEBI + slippage (from the configured
CostModel, so it differs correctly for INTRADAY vs DELIVERY) plus an execution
buffer — and ARMS only once a configurable profit trigger is met (default 3x
the round-trip cost, optionally also an ATR multiple). Never immediately after
entry: the crypto post-mortem showed tightening too early cuts winners short
(L-006). ``risk/trailing.py`` keeps the promoted ATR chandelier as the default
(unchanged behaviour) and adds percentage / structure / volatility modes behind
one interface; the profit-lock ladder applies in every mode. Strategies select
via a declarative ``meta.trail_mode`` — no strategy code changes.

## D-022 — Execution intelligence: canonical Opportunity, weighted ranking, adaptive cadence, portfolio decisions, dynamic sizing (2026-07-17)

Extended (not redesigned) the scanner + paper engine into the production
execution pipeline. The canonical ``Opportunity`` carries prices/risk geometry
(promoted risk engine), expectations, historical evidence stats, confidence
components, regime, liquidity, and cost. Ranking replaced confidence ordering
with a configurable weighted engine (``RankingWeights``/``RankingScales`` from
config; strategies without evidence score components at the NEUTRAL midpoint,
never fabricated); every ranking decision persists to the signal row (rank +
``_ranking`` breakdown). Scanning cadence adapts per strategy timeframe
(15m≈45s, 1h≈3min, 1d≈7min, configurable) and position management runs every
tick independently of scans. A PortfolioManager decides OPEN/SKIP/REDUCE/
REPLACE against capital, exposure, sector caps (the correlation proxy until
return-correlation evidence exists — explicit, not hidden), and a daily risk
budget; sizing is dynamic (risk-parity core via ``risk_based_stake``,
confidence-scaled, budget/concentration/hard-capped, returns 0 rather than
force a too-small position). A console dashboard renders the full system state
each tick. No ML anywhere; confidence stays evidence-based heuristics pending
calibration. 179 tests.

## D-021 — Paper engine is gated on real-data measurement survival (2026-07-16)

The Paper Trading Engine refuses any strategy whose evidence status is not
measured/validated/approved/paper — statuses only a REAL-data measurement run
can grant (synthetic runs persist "draft" explicitly). `allow_unmeasured`
exists solely for offline pipeline validation and logs loudly. This encodes the
project's core rule in the runtime itself: nothing trades, even on paper,
without measured evidence. No live orders exist anywhere in the codebase.

## D-020 — Production data source pivots to Angel One SmartAPI (2026-07-16)

Owner decision: SmartAPI (app + GitHub Pages redirect + static IP already set
up) replaces Kotak Neo as the production NSE source; CSV import becomes a
fallback. Unlike Kotak's SDK, SmartAPI has a documented HISTORICAL candle API
(getCandleData: 1-minute…1-day intervals, per-interval day limits, ~3 req/s),
so real backfill is first-class. Implemented against the official SDK
(verified from its source): TOTP login/refresh/logout/profile, scrip-master
instrument map, chunked+throttled candle download, ltp quote. Credentials come
ONLY from a local .env (stdlib loader, secrets masked via repr=False, missing
values reported by name with portal provenance). Kotak code is retained but
unused; the DataProvider abstraction meant zero changes to
ingestion/store/scheduler/scanner/research.

## D-019 — Measurement pipeline complete; all six candidates FAIL on synthetic data (2026-07-16)

Phase 5 completed the Research Engine (edge lab, outcome labeler, trade
simulator, NSE cost model, cost sensitivity, confidence calibration, league
table with PASS/BORDERLINE/FAIL verdicts). Verdict bars are the pre-registered
ones — D-007's 2x-cost hurdle judged on the day-clustered CI LOWER bound, the
SS7 profit-factor/expectancy floors, SS15 2x-cost survival, and a 30-signal
evidence minimum. **Controls prove the pipeline discriminates**: a planted-edge
strategy PASSes, pure noise FAILs. On the synthetic corpus all six candidates
FAIL (CI lower bounds below cost, PFs 0.62-1.14) — the correct result for
random-walk data and the demonstration that nothing is biased toward
implementation. Real verdicts await real NSE history (CSV import;
`scripts/run_measurement.py --csv-root`). The risk engine was promoted verbatim
from the archive; the simulator reproduces the archived TradeManager's
monotonic stop-ratchet over stored bars with session square-off for intraday.

## D-018 — Six strategy candidates implemented; confidence is heuristic until calibrated (2026-07-16)

Implemented the six researched strategies (pullback_15m, volexp_1h, orb_15m,
vwap_15m, nr7_daily, ema200_daily) as plug-ins on the Phase-1 interface: each
declares indicator prep, an edge-triggered vectorized signal, frozen params, a
component confidence score, regimes, and failure-mode metadata. The scanner now
merges multiple timeframes into ONE ranked opportunity list and records every
firing candidate to evidence with its component breakdown, duplicate-protected.
**Status: all six are CANDIDATES.** Per the frozen lifecycle none may advance
until the research engine measures its edge against costs (D-007 gate). Their
confidence scores are explicitly heuristic hypotheses — the crypto phase proved
hand-designed advisory scores can carry zero signal (L-003) — so every component
is persisted for later evidence-based recalibration, and the scanner's score_fn
seam is where the calibrated engine will replace them.

## D-017 — Indicator library is pure pandas; talib not carried forward (2026-07-16)

The archived crypto `indicators.py` used TA-Lib (a C dependency, painful on
Windows, unavailable in this venv). The promoted `core/indicators.py` implements
the same indicators in pure pandas — Wilder RSI/ATR/ADX use the identical
formulas already battle-tested in the validation regime labeler — and ports
`crossed_above`/`crossed_below` verbatim from the archive. Session-scoped
equity additions (session VWAP, opening range) reset per NSE session. The
archived talib version remains in git history (`archive/crypto-freqtrade/`,
removed in the repository cleanup) for reference.

## D-015 — Kotak Neo SDK has no historical-candle API; accumulate forward (2026-07-15)

**Evidence:** the official Kotak Neo v2 SDK (supplied by the owner) exposes no
historical OHLCV endpoint — grep of the SDK confirms OHLC only via
`quotes(quote_type='ohlc')` (current session) and the live websocket; `urls.py`
has no history route. **Decision:** `KotakNeoDataProvider.fetch_ohlcv` builds
today's daily bar from the quotes snapshot, and the incremental scheduler
accumulates a daily history going forward (idempotent via the store's upsert).
Bulk backfill uses `CsvDataProvider` (broker/vendor export); a future Kotak
charts endpoint can be wired if its official spec is provided. We do NOT invent
or hit an unofficial/undocumented history URL. Intraday bars require the
websocket collector (deferred).

## D-014 — Kotak Neo as the concrete NSE data provider (2026-07-15)

Implemented `KotakNeoDataProvider` against the Phase-2 `DataProvider` interface,
plus secure env-var config (secrets masked), a TOTP login session wrapper (lazy
optional SDK import, injectable client for tests), and an instrument-master
downloader (scrip master → symbol/token map, parquet cache). Reuses the whole
Phase-2 pipeline; no parallel implementation. Credentials are read only from the
environment and never hardcoded/logged. Validated with 13 mocked tests (a
`FakeNeoClient`); no SDK install or real credentials required.

## D-013 — Source-agnostic data layer; parquet store; NSE provider deferred (2026-07-15)

Market data flows through a single `DataProvider` interface, so NSE bhavcopy, a
broker API, a vendor, a CSV export, or the synthetic generator are
interchangeable and nothing downstream knows the source. Candles are stored in
**parquet** (per symbol×timeframe, upsert-dedup), never in the evidence SQLite
(consistent with D-010). The concrete live-feed `NseProvider` is a documented
wiring point, NOT implemented: it needs an owner source decision and credentials
(never handled by the assistant). `SyntheticDataProvider` + `CsvDataProvider`
exercise the entire pipeline offline in the meantime — the same "one real
testable implementation + honest stub" discipline used for brokers in Phase 1.

## D-012 — Phase 2 data & scanning layer built (2026-07-15)

Built the market-data ingestion (full + incremental, idempotent, quarantine on
bad data), the parquet market-data store, universe management + configurable
filters, the scanner engine (processes every eligible stock, unified ranked
opportunity interface, records every candidate to evidence), and the scheduling
jobs (full/daily/intraday). Reuses Phase 1 wholesale (filters, Scanner ABC,
Opportunity, StrategyProfile, EvidenceLogger, calendar, config). 64 tests +
an end-to-end integration smoke pass. No brokers/strategies/trading (by design).

## D-011 — Phase 1 platform foundation built (2026-07-15)

Built the reusable, market-agnostic foundation under `src/algo/` (see
docs/PROJECT_STATE.md): core primitives, the SQLite evidence store + logger, the
strategy plugin interface + registry, the universe-filter framework, the scanner
interface, and the research-engine skeleton. No strategies, brokers, data, or
trading — foundation only, by the approved plan. 32 pytest tests + the validation
self-test + an on-disk startup smoke all pass. Uncommitted pending owner review.

## D-010 — Evidence database is SQLite; market data stays in parquet (2026-07-15)

The evidence store (every signal, outcome, trade, evaluation, regime label, run)
is a single SQLite file — one machine, one writer, analytics not OLTP, pandas-
friendly, transactional, keepable forever. Candles are NEVER stored there; they
stay in parquet. This division keeps the evidence DB small enough to retain
indefinitely, which is the point (evidence is the product). No server database —
that would be speculative infrastructure.

## D-009 — Retire Freqtrade; adopt an `src/algo` package; archive crypto (2026-07-15)

Freqtrade is structurally crypto-only (CCXT brokers, no NSE path, no session
calendar) so it is retired, not adapted. The project moves to a standard
src-layout Python package (`src/algo`, editable install, pytest), and the crypto
code was preserved under `archive/crypto-freqtrade/` (now removed; it remains
available in git history). The
market-agnostic reuse candidates (indicators, risk_engine, trade_manager,
decision_engine) are flagged there for Phase-2 promotion. The validation package
was relocated into the platform and decoupled from `algo_core`.

## D-008 — Pivot to a research-first Indian equities platform (2026-07-15)

Owner-approved pivot from crypto to Indian equities, with the architecture
centered on **discovering** profitable opportunities (the Research Engine +
evidence database) rather than implementing preconceived strategies. Rationale:
D-007 — the crypto project engineered an excellent platform around an edge that
was never large enough. The new design makes measurement-before-implementation
an architectural gate, not a discipline. Full design approved across the
repository-audit, research-engine, and phase-1 design exchanges.

---

## D-007 — Stop strategy iteration; entry signal must be replaced (2026-07-14)

**Evidence (LEARNINGS L-009):** the entry edge was measured directly over 1,699
entries with exits ignored. It is real and statistically significant (gross
+13.3 bps at 180 min, 95% CI [+3.7, +22.7]; MFE/|MAE| 1.17 vs 0.97 random) but
**never exceeds the 20 bps round-trip cost at any horizon**, and the median
entry is negative even before fees.

**Decision: stop iterating on exits/risk/portfolio.** Three exit designs, two
full redesigns and a controlled single-variable experiment have all confirmed
the same arithmetic: gross expectancy ~3 bps/trade vs 20 bps cost. This is a
signal-strength problem, not a calibration problem.

**Options (owner decision required, none taken):**
1. Replace the entry signal with one whose gross edge exceeds ~2x costs.
2. Re-scope holding horizons far beyond the frozen SS7 30-120 min band - the
   edge grows with horizon (1.5 -> 13.3 bps over 30 -> 180 min) and may only
   clear costs at multi-hour/day holds. Requires a protocol amendment.
3. Shelve.

## D-006b — Gate 0 blocker resolved (2026-07-14)
v3's lookahead flag proven a Freqtrade tool artifact, not a strategy bias
(LEARNINGS L-008). All v1/v2/v3 results are valid. A latent robustness flaw is
recorded but deliberately NOT fixed under the analysis-only mandate:
`populate_indicators` silently drops columns when informative data is missing,
which suppresses signals and breaks freqtrade's bias tooling.

---

## D-005 — AdaptiveTrend v3: controlled exit refinement (2026-07-14)

Single-variable change vs v2: replaced the 5m EMA-cross structural exit with a
**confirmation-based 15m exit** (`ema_fast_15m < ema_slow_15m` AND
`close < ema_slow_15m`). Everything else from v2 held constant (anti-chase,
cost gate, tighter trailing, removed advisory scoring + RR gate). v1 and v2
left completely intact; all three strategies load side by side. No tuning, no
hyperopt - the exit reuses existing 15m EMAs with no new thresholds.

**Reasoning:** 15m is 3x coarser than 5m so routine pullbacks that hold above
the 15m structure cannot trigger it; requiring price below the 15m slow EMA is
the confirmation, so it only fires on a genuine structural break.

**Outcome - partially validated, design goal missed:**
* **Fixed the v2 catastrophe:** structural fires 1,469 -> 63; net -897 -> -82;
  overall -548 -> -421; PF 0.47 -> 0.60; win 32.4% -> 54.8%; MaxDD 55% -> 43%.
* **Missed its goal:** it does NOT exit earlier than the 1h version - it fires
  at a 605-min lag (vs v1's 250 min), because a full 15m death-cross plus price
  confirmation is a slow condition.
* **All three structural exits (1h/5m/15m) have a 0% win rate.** -> D-006.

## D-006 — Structural exits to be REMOVED in v4 (2026-07-14)

Evidence: three structural-exit variants across three timeframes have now been
tested; every one is net-negative with a **0% win rate** (1h: -56, 5m: -897,
15m: -82). None has ever produced a winning trade. The trailing stop is the only
profitable exit (v3: +553 USDT, 80% win). **Decision: v4 removes structural
exits entirely; the trailing stop + hard stop become the only exits.**

Caveat recorded: this alone will not make the strategy profitable. L-006 shows
all three versions are gross-flat before fees (per-trade gross edge ~3 bps vs
20 bps cost). v4 must also confront the cost/edge gap and the trail/stop
asymmetry (avg win +0.48 vs avg loss -1.03).

---

## D-004 — AdaptiveTrend v2 redesign (2026-07-14)

Built `AdaptiveTrendStrategyV2` alongside the intact v1, changing only the four
subsystems Phases D/E proved ineffective. Each decision and its validated
outcome:

| Change | Rationale (evidence) | Validated outcome |
|---|---|---|
| **Remove advisory scoring** (RSI/volume/volatility weights) | Phase E: winner score == loser score (AUC 0.51); no predictive value | Kept removed - no regression |
| **Remove risk_reward gate** | Phase E: near-constant ~1.5 (74.6% of trades), corr with outcome ~0.07 | Kept removed - no regression |
| **Add anti-chase gate** (reject `dist_fast_5m` > 0.3% or `mom_1h` > 1.5%) | Phase E: chasing extended moves harmful (AUC ~0.45) | **Helped** - initial stop-outs fell 314 -> 49 |
| **Add cost gate** (expected reward = 2xATR% must exceed 2x round-trip cost) | Phase E: entry edge ~17bps < 20bps cost; observed MFE ~2x ATR% | Neutral-to-helpful (part of the stop-out reduction) |
| **Tighter trailing** (1.25xATR @ +0.4%, lock ~50% of each tier) | Phase D: v1 captured only 43% of MFE, gave back 1.26%/trade | **Helped** - trailing win 72% -> 78.7%, +274 -> +421 USDT |
| **Replace 1h-EMA exits with 5m EMA-cross reversal exit** | Phase D: v1 objective exits lagged ~225min behind the peak | **FAILED CATASTROPHICALLY** - see D-004a |

### D-004a — The 5m EMA-cross exit hypothesis was wrong
Replacing the too-slow 1h exit with a 5m fast/slow EMA-cross reversal exit
**over-corrected into noise**: it fired 1,469 times at a **0.1% win rate** for
**-897 USDT**, ejecting positions on routine 5m pullbacks before either the
trend resumed or the trailing stop could lock profit. It also collapsed mean
MFE (1.15% -> 0.65%) by cutting trades before they developed. Net v2 result:
**worse than v1** (-54.8% vs -15.2%, MaxDD 55% vs 16%) despite two of the four
changes being beneficial. Kept in the repo as a recorded negative result; v3
must remove it and let the (tightened) trailing stop be the primary exit.

### Preserved (unchanged, proven valuable / structural)
MTF pipeline, indicator calculations, mandatory GO architecture, hard stop,
position sizing, the trailing MECHANISM, RegimeDetector, validation tooling.
v1 left completely intact (self-test still 84/84 PASS; Gate 0 still clean).

### Method note
All v2 values are hand-set from Phase D/E evidence and documented; **no
hyperopt, no parameter optimization** was used (per task constraint).

---

## D-003 — Validation protocol frozen (2026-07-14)
The historical-data + backtesting methodology was frozen as
`architecture/VALIDATION_RULES.md` (25 sections). Acceptance is risk-adjusted
(no fixed CAGR floor). Amendment requires explicit approval.

## D-002 — Risk-based sizing disabled by default (2026-07-13)
`RiskParams.enable_risk_sizing = False`; the strategy uses the fixed config
stake until sizing is validated. Enabling requires `stake_amount: "unlimited"`.

## D-001 — Single source of truth for thresholds (2026-07-13)
Every tunable lives in `algo_core/settings.py`; profile and engine read the
same params. Established after an audit found duplicated profile/engine
constants.
