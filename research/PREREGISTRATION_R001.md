# R-001 Pre-registration — Short-horizon event & microstructure effects

_Phase 14, 2026-07-18. Written and COMMITTED before implementation and before any
measurement (RESEARCH_STANDARDS §1.1). Measured under the FROZEN D-031 gate,
unchanged. Every parameter below is locked; nothing changes after a verdict._

## Why R-001, and why these three families

R-001 is the top-ranked registry item: short-horizon (2-15 day) effects, where
the frozen gate has statistical power (tom, 3-7d, CI-low −7.9 — the closest of 27
strategies to certification). The objective is INFORMATION, not count: three
INDEPENDENT mechanisms, each a distinct behavioural/flow driver, each with a
specific directional prediction — NOT a parameter sweep, NOT a re-test of a
rejected idea.

**Multiple-testing context (pre-registered):** exactly **3 hypotheses** are
tested this phase (30 lifetime across the project). The gate's per-hypothesis
false-positive rate is 5%, so ~0.15 false PASSes are expected by chance here. Any
PASS is PROVISIONAL pending pre-registered out-of-sample confirmation before any
promotion (STANDARDS §3) — it is not promoted in this phase.

**Shared rules:** timeframe 1d, long-only, product DELIVERY; platform-uniform
exits (D-006); per-symbol (not cross-sectional); universe NIFTY-500; judged by
the frozen selection-edge gate. Implemented via the frozen hypothesis framework
(`compile_hypothesis`) with entry logic defined locally in the research script —
the frozen components/compiler are NOT modified.

---

## F1 — Month-start inflow

- **Hypothesis:** buying at the close of the FIRST trading session of a calendar
  month and holding ~1 week earns a positive selection edge.
- **Economic rationale:** Indian monthly systematic-investment-plan (SIP) inflows
  and salary-driven flows are large, dated, and land at month-START — a different
  slice of the turn-of-month mechanism from tom_daily, which entered ~2 sessions
  BEFORE month-end. This tests month-START demand specifically, a distinct
  flow-timing hypothesis in the strongest calendar family.
- **Expected behaviour:** one entry per stock per month at month-start; a short
  positive drift over the following sessions.
- **Holding horizon:** 3–7 days. **`horizon_bars`=(3,5,7)**, **`max_hold`=7**.
- **Entry:** the first trading bar whose calendar month differs from the prior
  bar's month (edge-triggered, causal — month boundaries are known in advance).
- **Measurable prediction:** selection edge CI-low > cost (30.9 bps) at some
  pre-registered horizon.
- **Falsification:** selection CI-low ≤ 0 (indistinguishable from a random
  entry), OR point edge ≤ 0.
- **Expected power:** HIGH — ~130 month-starts × ~500 symbols, short horizon.
- **Frozen params:** entry = month-change; horizons (3,5,7).

## F2 — Overnight gap-fade reversal

- **Hypothesis:** buying after a sharp overnight DOWN-gap (open ≤ −2% vs the
  prior close) and holding a few sessions earns a positive selection edge as the
  overreaction partially reverses.
- **Economic rationale:** overnight gaps overshoot on liquidity/sentiment shocks
  and partially mean-revert. This is directly motivated by evidence: egap_daily
  showed up-gap CONTINUATION has NEGATIVE selection (−84 bps) — the opposite sign
  — which HINTS gaps fade. F2 tests the fade (the reverse mechanism) on the
  down-gap side, which is tradeable long-only. It is NOT a re-test of egap: it is
  the opposite prediction on the opposite gap direction.
- **Expected behaviour:** fires on down-gap days (many, across stocks and time);
  a short bounce over the next sessions.
- **Holding horizon:** 3–5 days. **`horizon_bars`=(3,5)**, **`max_hold`=5**.
- **Entry:** `open / prev_close − 1 ≤ −0.02` (causal — open and prior close are
  both known at the entry bar's close).
- **Measurable prediction:** selection edge CI-low > cost.
- **Falsification:** selection CI-low ≤ 0, OR point edge ≤ 0 (gaps do NOT fade —
  which, with egap, would mean down-gaps continue down).
- **Expected power:** HIGH — down-gaps are frequent across 500 × 11y, short
  horizon.
- **Frozen params:** gap threshold −2%; horizons (3,5).

## F3 — Monthly F&O expiry drift

- **Hypothesis:** buying ~2 sessions before the monthly derivatives expiry (the
  last Thursday of the month) and holding through/just after expiry earns a
  positive selection edge.
- **Economic rationale:** monthly F&O expiry concentrates rollover, unwinding and
  index-arbitrage flows in the expiry window — a dated, structural, India-specific
  microstructure event distinct from any calendar or gap mechanism. Derivatives-
  driven flow, not fund inflow.
- **Expected behaviour:** one entry per stock per month in the expiry window; a
  short drift through expiry.
- **Holding horizon:** 3–7 days. **`horizon_bars`=(3,5,7)**, **`max_hold`=7**.
- **Entry:** the first trading bar within 2 business days on/before the last
  Thursday of the month (edge-triggered; last-Thursday is a known calendar fact,
  causal).
- **Measurable prediction:** selection edge CI-low > cost.
- **Falsification:** selection CI-low ≤ 0, OR point edge ≤ 0.
- **Expected power:** HIGH — ~130 expiries × ~500 symbols, short horizon.
- **Frozen params:** window = 2 business days to last-Thursday; horizons (3,5,7).

---

## Pre-registered expectation

The turn-of-month family already came within 8 bps of certification (tom), so F1
(month-start) is the most likely of the three to clear; F2 and F3 are genuinely
open. The realistic outcome is 0–1 certifications; any certification is
provisional pending out-of-sample confirmation and is NOT promoted this phase.
The information gained — whether short-horizon flow effects on NSE clear the
frozen gate — decides whether R-001 continues, is archived, or needs event data.
