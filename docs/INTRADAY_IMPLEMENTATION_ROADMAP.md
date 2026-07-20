# INTRADAY IMPLEMENTATION ROADMAP — practitioner-faithfulness pass

_Written 2026-07-19 (Phase 4). Recommendations only — no code changed, no
backtests run. Every item below is a rule consistently recommended in the
practitioner literature cited in `IMPLEMENTATION_GUIDE.md`; none is an
optimisation, ML, AI-generated filter, or statistical feature engineering.
The strategy-owned execution model is the standard: every item lands as a
declared rule inside the owning strategy (entry-gate logic in the strategy's
own `prepare`/`entry_signal`, or fields on its `ExecutionSpec`), executed by
the existing strategy-agnostic engine. After each tranche, the validation is
the SAME standardized ₹50k backtest — rerun once, reported honestly, no
tuning loops._

## A. Corrections — make strategies faithful to practitioner use (ranked)

**A1. Time-of-day relative volume (RVOL).** Replace the rolling-20-bar
`volume_ratio` *in the entry gates that already require volume* (orb, cpr,
and the missing gates below) with volume relative to the same bar-of-session
average over the trailing ~20 sessions. This is the definition practitioners
actually mean by "1.5× volume"; the rolling-20 proxy systematically
mis-measures mornings and lunches. One shared helper in `core/indicators`,
used by each strategy's own `prepare`. *(Unblocked today. Threshold values
stay exactly as declared — only the metric becomes the practitioner one.)*

**A2. Index-alignment gate.** Long entries only when the NIFTY-50 intraday
tape agrees (index above its session VWAP — the simplest consensus form).
Universally recommended ("trade with the market": Elder, Fisher, Shannon,
prop practice). Implemented as a market-context frame the scanner/backtest
passes in; each strategy declares whether it requires alignment (all seven
practitioner forms do for longs). *(Blocked on data step 1: NIFTY-50 intraday
candles — see `DATA_CAPABILITY_REPORT.md` §3.)*

**A3. Entry time windows.** Per-strategy session windows as declared session
restrictions: breakout entries (orb, cpr) in the morning window
(~09:30–12:00); continuation entries (pullbacks, VWAP setups) until ~14:30;
no fresh entries after ~14:30 for anything (no runway before the 15:15
square-off). Consistent practitioner guidance; lands as `ExecutionSpec`
session fields. *(Unblocked today.)*

**A4. VWAP test/reclaim counting.**
- `vwap_pullback_15m`: take only the FIRST (optionally second) VWAP test of
  the session — the single most repeated VWAP-support rule, and the direct
  practitioner fix for this encoding's documented 5–9× over-firing.
- `vwap_15m`: cap reclaims at the first/second of the session; require the
  VWAP to actually be rising (slope), not merely price-above-share.
*(Unblocked today.)*

**A5. CPR day-type gate.** Trade the TC breakout only on NARROW-CPR days
(width low relative to its own trailing widths — Ochoa's trend-day tell and
the method's core selection rule), with the two-day relationship not bearish,
and skip when the open gaps far beyond the CPR (value abandoned). Currently
narrowness is merely scored in confidence. *(Unblocked today.)*

**A6. ORB day-quality gates.** Reject wide opening ranges (OR height vs
daily ATR — currently only scored), and encode the gap rule (skip longs on
large exhaustion gaps; modest with-trend gaps fine). *(Unblocked today.)*

**A7. Squeeze fidelity for `volexp_1h`.** Define the squeeze RELATIVELY —
bandwidth at/near the low of its own trailing history (Bollinger's own
definition; the absolute 0.03 threshold is the exact simplification he warns
against) — and add the consensus direction filter (with-trend breaks only)
and volume-expansion gate (currently confidence-only). *(Unblocked today.)*

**A8. First-pullback pattern completion.** Let the pullback be the common
1–3-bar orderly flag (entry above the PATTERN high) instead of exactly one
down-bar, with the standard depth (holds the level — already enforced) and
duration caps, plus the volume signature (contract in the flag, expand on
resume). *(Unblocked today.)*

**A9. Trend-strength gate for `pullback_15m`.** Gate on trend quality (EMA
separation floor or equivalent structure test — Raschke/Brooks practice),
and reject overextended entries; both currently only scored in confidence.
*(Unblocked today.)*

**A10. Breakeven-after-1R.** For the 2R-target strategies (vwap pair,
first-pullback), move the stop to breakeven at +1R — near-universal
defined-risk management in the cited playbooks; the engine already supports
breakeven mechanics (used by CPR's partial). Lands as an `ExecutionSpec`
field. *(Unblocked today.)*

**A11. Event-day avoidance.** Skip a symbol on its results day and skip
fresh entries on major macro days (policy/budget/election results).
Consistently recommended; **blocked on an earnings/results calendar source**
(SmartAPI candles cannot provide it — see data report §3 step 5).

## B. Optional enhancements (practitioner-supported, not required for faithfulness)

**B1. 5-minute ORB variant** (and a registered 60-minute initial-balance
variant) — both standard practice; *5m variant blocked on data step 3.*

**B2. Volatility-regime awareness.** India-VIX / daily-ATR regime flag as a
declared no-trade condition for breakout setups in dead-vol regimes.
*(Blocked on VIX series — data step 1.)*

**B3. Sector-strength alignment.** Longs in sector leaders when the sector
index is strong — common institutional practice; *blocked on a sector map
(instruments.sector is empty) + sector index data.*

**B4. Trailing for runners.** Higher-low / VWAP trailing as declarable trail
modes for trend-day runners (ORB/CPR third legs) — practitioner-standard;
the spec's `trail` field is the landing point.

**B5. Retest/limit entry mode for ORB.** Entering on the post-break retest of
the OR high (reduces chase; widely taught) — needs careful honest fill rules;
keep optional.

**B6. 1-minute data for future strategies** (gap-and-go, opening drive — both
on the target-20 missing list) — data step 4.

## Sequencing recommendation

1. **Tranche 1 (no new data):** A1, A3, A4, A5, A6, A7, A8, A9, A10 — then ONE
   standardized ₹50k rerun to publish the faithful baseline.
2. **Tranche 2 (small data step):** index + VIX series → A2, B2 → rerun once.
3. **Tranche 3 (bulk history):** 15m backfill → the same baseline over
   2016–2026; 5m → B1.
4. **A11 / B3** wait on their external data sources (separate acquisition
   decisions).

_Guardrails: thresholds come from the cited literature and are declared in
each strategy's own module next to its hypothesis; nothing is swept, and the
rerun is a single measurement per tranche, not a selection loop._
