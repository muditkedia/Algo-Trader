# IMPLEMENTATION SEQUENCE

_Written 2026-07-19. Planning only — nothing below is started until approved.
Inputs: `DATA_INFRASTRUCTURE_PLAN.md`, `MARKET_CONTEXT_FEATURES.md`,
`STRATEGY_DEPENDENCY_MATRIX.md`, `docs/IMPLEMENTATION_GUIDE.md` (practitioner
rules A1–A11 / B1–B6 in `docs/INTRADAY_IMPLEMENTATION_ROADMAP.md`)._

**Ordering principle (as directed):** shared market-context features first,
shared data expansion second, strategy-specific practitioner rules third —
one strategy at a time — with the standard ₹50,000 backtest and a documented
comparison after every strategy, stopping before the next.

---

## Stage 1 — shared market-context features (no new data required)

Build order follows the reuse counts in the dependency matrix (widest first),
each item = helper(s) in `src/algo/features/` + unit tests only. **No strategy
wiring, no behaviour change anywhere, full suite green after each step.**

| # | Item | Features | Serves |
|---|---|---|---|
| 1.1 | Features package skeleton + as-of join helper | `features/__init__`, `join.py` | — |
| 1.2 | Session clock & entry-window flags | F1 | 7 |
| 1.3 | Time-of-day RVOL (with special-session exclusion) | F2 (uses F4's whitelist) | 7 |
| 1.4 | Session type + special-session whitelist (+ expiry CSV schema) | F4 | 7 |
| 1.5 | Opening-gap classification | F3 | 6 |
| 1.6 | OR width metric + daily-ATR-as-of helper | F9 (+F11 groundwork) | 2 |
| 1.7 | ATR regime + trend strength | F11, F13 | 4 each |
| 1.8 | VWAP slope + VWAP test counter | F7, F8 | 2 each |
| 1.9 | CPR classification (width percentile, two-day relation, virgin flag) | F10 | 1 (method-defining) |
| 1.10 | Bollinger bandwidth percentile | F12 | 1 (+future) |

Market-side features that need context data (F5/F6 index alignment, F14 VIX
regime) are DESIGNED in stage 1 (interfaces + tests against synthetic frames)
but activate in stage 2.3. F16 (events) and sector alignment stay parked on
their data decisions.

*Stage-1 exit criteria:* all helpers tested (causality/truncation tests
included, matching the platform's lookahead-test convention); zero changes to
strategies, engines, or any recorded result.

## Stage 2 — shared data expansion (per `DATA_INFRASTRUCTURE_PLAN.md`)

Each step ends with `store_audit.py` green and a manifest entry. Order:

| # | Step | Unblocks |
|---|---|---|
| 2.0 | Probe: actual server-side depth per interval; VIX intraday availability | go/no-go facts for 2.2–2.5 |
| 2.1 | Instrument-layer index resolution (`NIFTY50`, `BANKNIFTY`, `INDIAVIX`) + `--context` download path + `store_audit.py` + manifest writer | everything below |
| 2.2 | Context series: indices + VIX (1d, 15m, 5m where available) | F5/F6, F14 |
| 2.3 | Activate market features + the runner/scanner context-stamping seam (`mkt_*`, `vix_*`, `session_*` columns) — **verify: stamped columns change no existing signal** (all current strategies ignore them) | index alignment & VIX regime become usable |
| 2.4 | 15m backfill to the verified maximum (~2016) for the 99-symbol universe | regime coverage at the native timeframe |
| 2.5 | 5m history for the universe | 5-minute variants (B1), finer windows |
| 2.6 | *(optional, deferred by default)* 1m for a liquid subset | future gap-and-go/opening-drive work |

*Stage-2 exit criteria:* audits clean; documented depth/quality findings for
pre-2023 data (corporate-action caveat surfaced explicitly); no strategy or
result change. **Backtests are NOT rerun in this stage** — the corrected
baseline (`intraday_50k_backtest_fixed.md`) remains the comparison anchor.

## Stage 3 — strategy-by-strategy practitioner upgrades

**One strategy per batch. Never combined.** Each batch:

1. Declare the strategy's practitioner rules (from `IMPLEMENTATION_GUIDE.md`
   §its-section) in the strategy's OWN module — entry gates call
   `algo/features/` helpers inside its `prepare`/`entry_signal`; management
   rules (breakeven-at-1R, windows) land on its `ExecutionSpec`. Thresholds
   are the literature's declared values, written next to the rule with its
   source; no sweeps.
2. Unit tests for each new gate (fire/don't-fire on crafted sessions +
   causality).
3. Run the **standard ₹50,000 backtest** (same dataset window, costs,
   slippage, universe as the corrected baseline; if stage 2.4 has extended
   history, report BOTH the original 2023–2026 window — the like-for-like
   comparison — and the full window).
4. Document the comparison: per-gate signal attrition (signals before/after
   each conjunctive gate — exact counts), trades/win rate/avg win/avg loss/
   net/PF/max DD/avg-per-trade/final capital vs the previous version, and a
   plain statement of what changed and why (which practitioner rule did
   what).
5. **Stop for review before the next strategy.**

Proposed order (High-priority gaps first; each batch independent, so the
order can be changed at review):

| Batch | Strategy | Core practitioner items (guide ref) |
|---|---|---|
| 3.1 | `orb_5m` **complete** | STRAT-01: bidirectional 5m OR, same-slot RVOL, regime/confidence gates, collared fill, partial/BE/chandelier |
| 3.2 | `orb_retest_5m` **complete** | STRAT-02: wave-extension/retest state, depth/time invalidation, directional pivot stop, grade target |
| 3.3 | `cpr_breakout_15m` | narrow-CPR gate + two-day relation + gap context (F10/F3), RVOL, index alignment, morning window |
| 3.4 | `vwap_15m` | slope requirement, reclaim cap, range-day rejection, index alignment, window |
| 3.5 | retired `first_pullback_15m` | Superseded by completed STRAT-02 `orb_retest_5m`; no duplicate implementation retained |
| 3.6 | `pullback_15m` | trend-strength gate (F13), overextension rejection, alignment, window |
| 3.7 | `volexp_1h` | relative bandwidth (F12), direction filter, volume gate, VIX regime |

Rationale for 3.1 first: ORB exercises the widest slice of the new
infrastructure (F1, F2, F3, F5/F6, F9) — it validates the whole feature chain
before the narrower batches; 3.2 next because its test-count cap fixes the
library's largest documented distortion (5–9× over-firing).

## Stage 4 — the recurring backtest & comparison protocol (per batch)

- Config pinned: ₹50,000/trade, `NseEquityCostModel` defaults (2 bps/side
  slippage), NIFTY-100 universe file, strategy-owned execution engine —
  identical to `intraday_50k_backtest_fixed.md`.
- One run per batch. No parameter iteration inside a batch: if a declared
  rule looks wrong in the results, the finding goes in the review notes and
  any change is its own reviewed batch.
- Comparison artifact per batch:
  `user_data/backtest_results/reports/<strategy>_practitioner_vX.md` with the
  before/after table, per-gate attrition, and the changed-behaviour
  narrative.
- The library documentation (`INTRADAY_STRATEGY_LIBRARY.md` §2 entry and the
  guide's field 15/16) is updated in the same batch, so docs never trail
  code.

## Standing constraints (all stages)

- No optimisation, no ML/AI-generated filters, no statistical feature
  engineering, no parameter sweeps.
- Frozen research engine, recorded verdicts, and existing reports are never
  modified.
- Every stage/batch ends with the full test suite green.
- Nothing is committed without explicit approval; every stop point above is
  a review gate.
