# Research Post-Mortem

_Phase 12, 2026-07-18. An objective review of every strategy the project has
implemented and measured under the frozen validation framework (D-031). This is
a completed research result, not a failure: 27 diverse strategies across ~10
years of NIFTY-500 data, none statistically certified, and a clear map of WHY._

The methodology is frozen and is NOT revisited here. This document reviews the
process and results and redirects future effort.

---

## Part A — Post-mortem by hypothesis family

All 27 strategies FAIL the frozen gate (selection edge's 95% CI lower bound must
exceed the round-trip cost). The verdicts are not uniform noise, though — the
selection POINT edge and CI width vary systematically by family and, above all,
by HORIZON. Numbers below are the selection edge (point / 95% CI-low, bps) at
each strategy's best-selection horizon; cost is 30.9 bps (delivery) / 12.2
(intraday).

### Momentum & relative strength — 6 core (+ adjacencies)

| strategy | mechanism | sel point | sel CI-low |
|---|---|---|---|
| tsmom_daily | own-trend (time-series) sign | −19 | −71 |
| hi52_daily | 52w-high absolute threshold | −9 | −100 |
| xsmom_daily | cross-sectional 12-1 rank | **+149** | −343 |
| resmom_daily | residual (market-adj) rank | **+112** | −363 |
| dualmom_daily | relative + absolute gate | **+149** | −343 |
| hi52rank_daily | 52w-high cross-sectional rank | **+252** | −762 |

- **Common assumptions:** past relative performance persists; winners keep
  winning over weeks-months.
- **Common failure mode:** at 21-63-day horizons the selection CI is 300-760 bps
  wide — the ~150 bps edge cannot be resolved from zero.
- **Recurring strength:** the CROSS-SECTIONAL forms have large, positive,
  consistent point edges — the effect is directionally real on NSE (the
  literature's sign). The PER-SYMBOL forms (tsmom time-series sign, hi52
  threshold) do NOT — their point edge is negative. **Relative rank carries
  signal; own-trend level does not.**
- **Recurring weakness:** long-only truncates the loser leg; momentum-crash
  regime risk; formation windows burn scarce early history.
- **Exhausted?** As a PHENOMENON, no — cross-sectional momentum is the
  strongest directional signal in the whole project. As a LONG-HORIZON tradeable
  under the gate, yes — the horizon is wrong for the available statistical power.

### Trend following — 4

| ema200_daily +12/−26 · vwap_15m +0.4/−3.2 · triple_screen +23/−129 · breadth_regime +191/−310 |

- **Assumptions:** an established price trend continues.
- **Failure mode:** per-symbol trend has near-zero selection edge (ema200 +12,
  vwap +0.4) — being in an uptrending stock is not better than random once drift
  is removed. breadth_regime's +191 point comes from the momentum breakout leg,
  not the trend filter.
- **Strength/weakness:** simple, robust to overfit; but no cross-sectional edge.
- **Exhausted?** As tested (per-symbol trend), **yes** — no selection signal.

### Breakouts — 3

| orb_15m +1.1/−1.1 · donchian55 +23/−380 · stage2 +383/−883 |

- **Assumptions:** a new N-period high marks a supply/demand imbalance that
  continues.
- **Failure mode:** intraday ORB is cost-dead; the daily breakouts have very wide
  CIs. But **stage2 (breakout + rising-30w-MA + volume) has the 2nd-largest point
  edge in the project (+383)** — structural breakouts are directionally powerful.
- **Exhausted?** Directionally strong, statistically unresolved at long horizon.
  The structural/volume-confirmed breakout deserves a shorter-horizon re-test.

### Volatility — 5

| nr7 +14/−11 · squeeze −20/−82 · volexp_1h +0.7/−8.5 · lowvol −29/−130 · maxret +9/−113 |

- **Assumptions:** compression precedes expansion (nr7/squeeze); low-vol earns a
  premium (lowvol); extreme-return names underperform (maxret).
- **Failure mode:** compression predicts a move, not its SIGN (squeeze −20 point
  — it selects BAD bars); expansion is cost-dead.
- **Notable falsification: the low-volatility anomaly is INVERTED on this sample**
  (lowvol point −29). On a 2015-2026 momentum-led NSE bull, low-vol UNDER-performs
  — the opposite of the developed-market factor.
- **Exhausted?** Compression **yes**; low-vol **inverted** (a real finding).

### Mean reversion — 3

| pullback_15m −1.0/−1.5 · wyckoff_spring +64/−109 · xsrev +24/−111 |

- **Assumptions:** oversold/failed-breakdown/loser stocks rebound.
- **Failure mode:** cost-hostile (short holds), and profits concentrate in
  illiquid names (Avramov-Chordia-Goyal, as pre-registered). wyckoff's +64 point
  (best per-symbol in batch 1) does not survive its CI.
- **Exhausted?** Cost-structurally hard; some directional signal (wyckoff, xsrev)
  but the gate cannot certify it at these horizons.

### Factor investing — 3 (bab, lowvol, combo)

| bab +232/−854 · lowvol −29/−130 · combo +33/−329 |

- **Assumptions:** systematic factors (beta, volatility) earn risk premia.
- **Finding:** BETA (bab +232) is directionally strong — low-beta out-points
  high-beta cross-sectionally; but VOLATILITY (lowvol) is inverted. The composite
  (combo +33) dilutes rather than strengthens, because one of its two legs
  (low-vol) is inverted here.
- **Exhausted?** Beta directionally real; the raw-return gate + long-only + long
  horizon make it uncertifiable.

### Volume & liquidity — 2

| hvol +109/−82 · illiq +392/−1070 |

- **illiq has the LARGEST point edge in the entire project (+392)** and the
  largest CI (−1070) — the illiquidity premium is directionally the strongest
  effect AND the noisiest (it lives in the least-liquid, highest-variance names).
- **Exhausted?** No — directionally the strongest family, but its variance and
  the cost-model's optimism in illiquid names (pre-registered) make it both
  uncertifiable and unsafe to deploy without a better cost model.

### Calendar — 1 (the standout)

| **tom_daily +75 / −7.9** |

- **The single most promising result across all 27.** A turn-of-month calendar
  effect: selection +75 bps (clears the 30.9 cost on the POINT) with CI-low
  −7.9 — a hair from certification, because it is SHORT-horizon (3-7 days) with a
  HUGE sample (53,645 signals). rel-PF 1.46.
- **Exhausted?** **No — under-explored and the clearest signpost** to where the
  frozen gate has statistical power: short horizons, many near-independent events.

### Stage analysis — 1 · Event-driven — 1

- stage2 (+383/−883): see Breakouts — directionally powerful, unresolved.
- egap (−84/−300): the price-only PEAD PROXY FAILED (negative point). This does
  NOT falsify PEAD — it falsifies the gap-as-proxy. True event-driven needs
  earnings data (a knowledge gap, Part B), and is UNEXPLORED, not exhausted.

### Cross-cutting patterns (all 27)

1. **Selection edge scales with the mechanism, CI scales with the horizon.**
   Cross-sectional rank → large positive point edge; long horizon → enormous CI.
   The two pull against each other, and horizon wins.
2. **Effective n is the independent-block count, not the trade count.** illiq's
   1,540 trades over 126-day holds carry less statistical weight than tom's
   53,645 over 3-7 days.
3. **Nothing beats buy & hold** (long-only, 11-year bull) — every excess-vs-B&H
   is negative.
4. **Confidence heuristics carry no signal** (|corr| ≤ 0.06), now a FOUR-TIME
   finding (L-003, D-026, D-030, L-012). Stop hand-designing them.
5. **Directional truth ≠ certifiable edge.** The gate correctly refuses to
   certify economically-large but statistically-unresolved point edges. That is
   the framework working, not failing.

---

## Part B — Knowledge-gap analysis (identified, NOT implemented)

Families/techniques the project has NOT explored, that the results argue are
now the higher-value frontier:

| Gap | Why it matters given the evidence | Data/infra needed |
|---|---|---|
| **Short-horizon event microstructure** | tom (3-7d) is the only near-certification; the gate has power here | none (have it) |
| **True event-driven (earnings/PEAD)** | egap proxy failed, but the real anomaly is the strongest in the literature and untested here | earnings calendar + actuals/estimates |
| **Index-event flows (add/delete, rebalance)** | predictable price-insensitive demand; short-horizon, high-power | index membership-change calendar |
| **Portfolio construction / position sizing** | every strategy was equal-notional single-name; a factor's edge may only survive as a diversified portfolio, not per-trade | none — a measurement MODE, not a gate change |
| **Multi-factor ranking / ensembles** | single factors are directionally real but noisy; combining orthogonal signals is standard practice (combo tried only 2, one inverted) | the hypothesis framework (Part C) |
| **Regime switching / conditional exposure** | breadth_regime hinted; conditioning WHICH factor by regime is untested | breadth/market-internal series (have breadth) |
| **Market internals / breadth-driven allocation** | breadth is computable (built in Phase 11) but only used as a filter, not an allocator | none |
| **Relative-volume / order-flow proxies** | hvol (crude) was directionally positive; finer volume microstructure untested | intraday volume (have NIFTY-100) |
| **Inter-market / cross-asset signals** | NSE vs global indices, INR, commodities, rates — none tested | external data feeds |
| **Adaptive / walk-forward parameter selection** | all params were frozen point-choices; Mode-B (validated adaptation) is deferred infra | walk-forward optimiser (VALIDATION_RULES §10.1) |

**The two highest-value gaps** (Part E ranks them): short-horizon
event/microstructure effects (power exists, no new data) and true event-driven
data (unblocks the strongest untested anomaly).

---

## Part C — Research-engine evolution (IMPLEMENTED)

Delivered as working, tested infrastructure (not just a design):
`algo/research/hypothesis.py` + `algo/research/components.py`.

A hypothesis is now DECLARED, not hand-written:

```python
Hypothesis(name="mom_6_1", family="momentum",
           hypothesis="6-1 cross-sectional momentum, top decile",
           metric=components.momentum(126, 21), cross_sectional=True,
           horizon_bars=(21, 42, 63), max_hold_bars=63, min_history=160)
```

`compile_hypothesis(spec)` turns it into a real `StrategyProfile` that plugs into
the existing cross-sectional seam, measurement and frozen gate UNCHANGED.
Reusable components — metrics (momentum, volatility, reversal, max-return,
close-to-high, Amihud, RSI), filters (min-price, uptrend, positive-metric,
min-volume), entries (new-high, gap-up), and AND-composition — mean a new idea is
a few lines and a GRID of ideas is a loop (`compile_all`).

**Proven by equivalence tests:** a compiled momentum hypothesis produces
BIT-IDENTICAL signals to the hand-written `xsmom_daily`, and a compiled breakout
to `donchian55_daily`. So the framework expresses the SAME ideas the pipeline
already judges — faster, with no new methodology and no behavioural surprise.
Compiled hypotheses are `enabled=False` research objects, constructed on demand
by a research script — they are never auto-discovered into the live library, so
this adds no tradeable strategy and no promotion path. This is the requested
"generate large numbers of structured hypotheses without duplicating code."

---

## Part D — Strategy composition (DESIGN ONLY, not implemented)

The evidence motivates this precisely: 12 of 13 cross-sectional signals are
directionally positive and 11 beat random on average, but each is individually
too noisy to certify. Combining weak-but-directional signals is the natural next
question. Designs, in increasing ambition:

1. **Rank aggregation (recommended first).** Each hypothesis emits a
   cross-sectional percentile per stock per day (the framework already computes
   these via `composite_percentile`). Average the percentiles of K orthogonal
   signals into one score; sort the composite. This is exactly `combo` generalised
   to K factors, and it is measurable under the frozen gate with NO change — the
   composite is just another `metric`. **Key design rule: only combine signals
   that are (a) directionally positive and (b) low-correlated** (momentum × beta ×
   liquidity, NOT momentum × momentum). Weighting stays FIXED and pre-registered
   (equal, or inverse-historical-volatility) — never fit, or it becomes
   overfitting.
2. **Weighted voting / ensemble.** Each signal votes long/flat; enter names with
   ≥ M votes. Simpler than rank aggregation, coarser. Same frozen-gate
   measurement. The threshold M is pre-registered, not tuned.
3. **Portfolio-level evaluation (the deeper change).** The gate judges per-entry
   selection edge. A factor's real edge may only appear as a DIVERSIFIED PORTFOLIO
   (long the whole decile, monthly rebalanced), whose month-to-month return series
   has ~130 near-independent observations — potentially narrower CIs than the
   per-trade view. This is a new measurement MODE (a portfolio backtest with its
   own CI), not a change to the promotion statistic, and is the single most
   likely way the directionally-real factors could ever certify. It is the
   highest-value item that does NOT touch the frozen gate.

None implemented (per the phase's instruction). Item 3 is the strongest lead and
is ranked in Part E.

---

## Part F — Final assessment

**Conclusively learned:**
- On Indian large/mid-cap equities (NIFTY-500, 2015-2026), NO per-trade signal —
  across momentum, trend, breakout, volatility, mean-reversion, factor,
  calendar, volume, liquidity, stage, event-proxy — clears the drift-adjusted
  cost hurdle with statistical confidence. 27/27.
- Cross-sectional RANK signals are DIRECTIONALLY real (consistent positive point
  edges, beat random); per-symbol LEVEL/own-trend signals are not.
- The binding constraint is statistical POWER at multi-week/month horizons on a
  single 11-year market — not strategy choice. More correlated stocks do not add
  power to a long-horizon bet.
- The low-volatility anomaly is INVERTED on this sample; intraday is cost-dead;
  confidence heuristics carry no signal (4×).

**Falsified assumptions:** that a larger/deeper corpus would certify long-horizon
factors (it did not — D-032); that hand-designed confidence scores predict
outcomes; that the low-vol premium transfers to 2015-2026 NSE; that a price-only
gap is an adequate PEAD proxy.

**Untested (remain open):** true event-driven (earnings, index events);
short-horizon microstructure beyond tom; portfolio-level factor evaluation;
multi-factor ensembles; inter-market signals; adaptive/walk-forward selection.

**Is the framework sufficient?** For JUDGING, yes — it is correctly sized
(drift/​random cannot pass, regression-tested) and it has now been exercised on
27 strategies with reproducible, deterministic results. Its known limitation
(low power at long horizons, the unpaired difference CI) is a property of the
DATA, not a bug — no single 11-year market can power a 6-month-horizon CI, and
the methodology is frozen by decision.

**Would more history materially help?** For long-horizon factors, only
marginally — SmartAPI already gives ~11 years, near its limit, and independent
long-horizon periods grow linearly and slowly. For SHORT-horizon effects, the
existing data already provides tens of thousands of near-independent events;
power is not the constraint there. So more history is NOT the high-value lever;
CHOOSING powered horizons (and acquiring EVENT data) is.

**Ready to transition to automated research?** Yes. The manual per-strategy era
has hit diminishing returns — 27 hand-written strategies established the map, and
the marginal hand-written strategy now teaches little. The hypothesis framework
(Part C) makes structured, high-throughput exploration cheap, and the frozen
gate makes it safe. The project should pivot from writing strategies to
GENERATING and SCREENING hypotheses at powered horizons, plus acquiring the one
data class (events) that unblocks the strongest untested anomaly.
