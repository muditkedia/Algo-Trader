# Implementation Roadmap

_Phase 8.5, 2026-07-17. The formal, evidence-driven ordering of the candidate
library (`research/CANDIDATE_LIBRARY.md`). Nothing here is implemented; the next
phase implements exactly ONE strategy._

---

## 1. How this roadmap was produced

Every candidate was scored on the twelve dimensions below. Scores are
qualitative (L/M/H) and each traces to something checkable — a cited study, a
measured number from this platform's own evidence DB, or a verified property of
the codebase. **No dimension uses advertised win rates.**

| Dimension | What it means here | Grounding |
|---|---|---|
| Hypothesis quality | Is there a causal mechanism, or just a pattern? | library §3 per-candidate |
| Supporting evidence | Peer-reviewed replications, out-of-sample, cross-market | references |
| Contradictory evidence | Published rebuttals, decay studies, cost studies | counter-references |
| Independent studies | Distinct author groups (not one book, one vendor) | references |
| India suitability | India-specific evidence, or structural transfer argument | IIMA factor library etc. |
| Cost sensitivity | D-007 hurdle ÷ expected move per round trip | library §1 arithmetic |
| Holding horizon | Longer = cost-survivable but sample-poor (§2 of library) | declared per candidate |
| Data availability | Everything needed is in the store today? | verified in §2 of library |
| Implementation complexity | Parameters = overfitting surface; state = bug surface | interface audit |
| Statistical robustness | Sample size the corpus can actually provide at this horizon | 877-bar arithmetic |
| Measurement reliability | Will the D-028 block CI have enough independent blocks? | block arithmetic |
| Framework compatibility | Expressible on `entry_signal(df) -> Series` today? | verified per candidate |

Two facts from this phase's own engineering discipline the ordering:

1. **The corrected long-horizon gate (D-028).** With overlap honestly resampled,
   ema200_daily's CI-low at just an 8-day horizon fell 23.4 → 6.2 bps. At the
   multi-week horizons this roadmap targets, CIs will be wide, and the number of
   independent blocks — not the number of signals — is the real sample size:
   ~870 trading days gives ~40 independent 20-day blocks, ~14 independent
   60-day blocks. **Verdict expectations are calibrated to this**: the realistic
   good outcome for most candidates below is a well-measured gross edge and a
   FAIL/BORDERLINE verdict, not a PASS.
2. **The multiple-testing budget.** The evidence DB registers every strategy
   ever measured. Six are on record; this roadmap adds up to ten more. At a 95%
   bound, ~16 candidates ≈ 0.8 expected false PASSes. Any PASS must therefore be
   reported alongside the count of candidates measured to date, and a
   **repeat-measurement on new data as it accrues** (the store grows daily) is
   the standing control. This is reporting discipline, not a rule change — the
   pre-registered bars are untouched.

---

## 2. Tier assignments (Part A)

### IMPLEMENT FIRST — next three phases, one strategy each

| # | Candidate | The case, in one paragraph |
|---|---|---|
| 1 | **B1 Time-series momentum (12-1)** | Highest evidence quality of anything implementable today: three decades of replications across markets and asset classes (Moskowitz/Ooi/Pedersen 2012; Asness et al. 2013), an India-specific factor literature (IIMA), a causal story (under-reaction), and essentially two parameters (formation, skip). Fully expressible per-symbol. LOW cost sensitivity (1–3 month holds on 10%+ moves). Its weakness is statistical robustness — ~3 independent 12-month formation periods — which is precisely why it must be measured with the D-028 CI and reported honestly. |
| 2 | **A3 52-week-high proximity** | George & Hwang (2004, *JF*) plus a real behavioural mechanism (anchoring); **parameter-free** in threshold form, so the measurement tests the market, not an encoding; per-symbol today; LOW cost sensitivity. The purest possible second data point on the "longer horizon" thesis, deliberately different from B1's own-return signal. |
| 3 | **D2 Earnings-gap continuation** | The cheapest route to the best-documented anomaly on the blocked list (PEAD — Ball & Brown 1968, Bernard & Thomas 1989): a price/volume proxy needing zero new data. Distinct hypothesis family (event under-reaction), 20–60-day holds, LOW–MED cost sensitivity. Weakness: proxy contamination (non-earnings gaps), stated up front. |

### IMPLEMENT SOON — after the first three verdicts exist

| # | Candidate | Reasoning |
|---|---|---|
| 4 | **B3 Donchian 55-day breakout** (plain variant) | Canonical trend entry, one parameter, fat-tail payoff suits fixed cost. Sequenced after B1 because both are trend-family — B1's verdict is evidence about whether NSE large-caps trend at all; if B1's gross edge is ~zero, B3's prior drops and cheaper candidates move up. |
| 5 | **H1 Wyckoff spring** | The only trapped-liquidity/price-action hypothesis in the library; per-symbol today; LOW–MED cost. Practitioner-only evidence, so it earns its slot from hypothesis diversity, not literature weight. Parameters must be frozen before measurement. |
| 6 | **C2 Weekly squeeze** | Represents volatility compression with the FEWEST free parameters (Bollinger-inside-Keltner is fully objective) — chosen over C1 VCP for exactly that reason: same hypothesis, smaller overfitting surface, more reliable measurement. The directionless-expansion risk (volexp_1h's failure mode) is pre-registered as the expected failure. |
| 7 | **D3 High-volume return premium** | Peer-reviewed (Gervais et al. 2001, *JF*), volume already in the store, per-symbol. MED cost sensitivity (modest effect size) keeps it behind the LOW-cost candidates. |
| 8 | **B7 Elder triple screen** | Multi-timeframe pullback continuation — a hypothesis family nothing above covers. Trade-book evidence only; the weekly-resample lookahead trap needs a dedicated test. After #7 because its evidence quality is lower. |
| — | **Cross-sectional seam** (platform task, not a strategy) | Built here, between #8 and #9: an additive `prepare_cross_section` hook. Gates #9 and #10. |
| 9 | **A1 Cross-sectional momentum (12-1)** | THE best-documented equity anomaly, with India-specific factor evidence. Only its interface dependency keeps it out of tier 1. First strategy through the seam. |
| 10 | **E1 Low volatility / BAB** | Structural (leverage-constraint) explanation, lowest turnover in the library, India-specific studies. Risk-adjusted claim vs raw-return gates is a real mismatch — pre-registered: it is measured on the same bars as everyone, and a raw-return FAIL with strong risk-adjusted numbers is itself a finding worth having. |

### IMPLEMENT LATER — real hypotheses, dominated for now

| Candidate | Why later |
|---|---|
| A2 Residual momentum | Needs seam + a factor model — more machinery, more researcher choices; measure A1 first. |
| A5 Dual momentum | A1 + B1 composition; measure the components before the composite. |
| C1 Minervini VCP | Same hypothesis as C2 with many more free parameters; only if C2 shows signal. |
| B2 200-day MA trend | Closest relative of the rejected ema200_daily; must be a NEW pre-registered candidate, and the Sullivan/Timmermann/White data-snooping rebuttal caps its prior. |
| B4 Weinstein Stage 2 | Discretionary "base/stage" encoding = large overfitting surface; B3+C2 cover its mechanical core. |
| C3 NR7 multi-week | nr7_daily already FAILED at 8 days with CI-low now −13 bps; a longer-horizon re-cut needs its own pre-registration and a better prior than this. |
| G2 Turn-of-month | Real India-specific mechanism (SIP flows) but ~42 events in the corpus and MED–HIGH cost share. |
| F1 Short-term reversal | Avramov/Chordia/Goyal: the profit largely IS the transaction cost. Measure only if the platform ever trades at materially lower cost. |
| F2 RSI(2) | Vendor-authored evidence, cost-hostile holds; lowest prior in the implementable set. |
| E2 Low idiosyncratic vol | E1 first; residual version only if total-vol version shows something. |

### NOT CURRENTLY FEASIBLE

| Candidate | Blocker | What would unblock it |
|---|---|---|
| D1 PEAD | No earnings calendar/surprise data | **The highest-value data acquisition available** — an earnings-dates feed unblocks the strongest blocked hypothesis. |
| A4 Sector rotation | `instruments.sector` 0/99 populated | A sector map ingested into the instruments table. |
| D5 Index inclusion/deletion | No membership-change history | An index-events calendar. |
| F3 Long-term (3–5y) reversal | Needs 6–10y of data; corpus is 3.5y | Time. Not measurable on this corpus, in principle. |
| F4 Pairs / stat-arb | Long-only platform | Shorting support — an owner-level scope decision, not a small change. |
| G1 Overnight premium | Cost arithmetic: ~few bps/night vs 30.9 bps delivery round trip | Nothing — no data or platform change alters the arithmetic. |
| G3 Amihud illiquidity | The premium is compensation for the very costs paid to harvest it; NIFTY-100 has little of it | A different universe AND an impact-aware cost model. |
| D4 Gap-and-go (intraday) | 24.4 bps hurdle vs fraction-of-percent moves — D-026's category death | Nothing at current cost structure. |

---

## 3. The diversified queue (Part B) with Part F detail

Ten strategies, ten distinct hypotheses. Coverage against the requested
categories: trend (B3), momentum (B1, A1), breakout (B3/C2), mean reversion —
represented by the trapped-seller reversal H1, the only reversal form whose cost
arithmetic survives §1 — volatility (C2), volume (D3), relative strength (A3),
price action (H1), gap behaviour (D2), institutional-style (A1, E1).

Survival probabilities are qualitative and calibrated to a 0-for-6 base rate on
this platform and the corrected D-028 gate. "Moderate" is the ceiling; nothing
gets more.

| Q# | Strategy | Effort | Research value | P(survive honest measurement) | Primary failure risks | Horizon | Data | Dependencies |
|---|---|---|---|---|---|---|---|---|
| 1 | B1 Time-series momentum | **S** (1 phase; ~2 params + tests) | **Very high** — first-ever multi-week edge measurement on this platform; calibrates the whole long-horizon thesis | **Low-moderate** — best-documented effect, but ~3 independent formation periods and a wide D-028 CI | Momentum crash regime; 2023–26 window is one market cycle; formation eats 1y of corpus | 1–3 mo | store as-is | none |
| 2 | A3 52-week high | **S** | **High** — parameter-free second data point on anchoring/under-reaction | **Low-moderate** | Concentrates in extended names; fails at tops; threshold form is weaker than the published rank form | 1–6 mo | store as-is | none |
| 3 | D2 Earnings-gap continuation | **M** (gap/volume thresholds need pre-registration) | **High** — only event-family test available without new data | **Low-moderate** | Proxy contamination (non-earnings gaps); gap-day fill assumptions; fewer signals (~quarterly per symbol) | 20–60 d | store as-is | none |
| 4 | B3 Donchian 55-day | **S** | **Medium-high** — cleanest breakout test; informative even as FAIL | **Low-moderate** | Range-market false breakouts; tail-dependent payoff needs samples the corpus may not give | wks–mo | store as-is | B1 verdict informs prior |
| 5 | H1 Wyckoff spring | **M** (range/spring params must be frozen first) | **Medium-high** — only trapped-liquidity hypothesis; genuine diversification | **Low** — practitioner evidence only | Encoding risk (testing my parameters, not Wyckoff); springs are rare → sample size | 2–8 wk | store as-is | none |
| 6 | C2 Weekly squeeze | **S–M** | **Medium** — objective compression test; volexp_1h's hypothesis at survivable scale | **Low** — expansion is directionless (the known killer) | Direction ambiguity; weekly bars shrink the sample | wks | store as-is | none |
| 7 | D3 High-volume premium | **S** | **Medium** | **Low** — modest published effect vs MED cost share | Effect decay since 2001; US evidence only | 5–20 d | store as-is | none |
| 8 | B7 Elder triple screen | **M** (weekly resample + lookahead test) | **Medium** — only multi-timeframe hypothesis | **Low** | Weekly-bar lookahead; pullback thesis's bad track record here; book-only evidence | dys–wks | store as-is | none |
| 9 | A1 Cross-sectional momentum | **M** strategy + **M** platform (seam) | **Very high** — the literature's strongest implementable anomaly | **Low-moderate** (best prior of all, same sample limits) | Momentum crashes; crowding; seam correctness itself | 1–3 mo | store as-is | **cross-sectional seam** |
| 10 | E1 Low volatility | **M** | **Medium-high** — structural explanation; lowest turnover | **Low** on RAW-return gates (its claim is risk-adjusted) | Gate mismatch (pre-registered); bull-window handicap 2023–26 | mo–qtr | store as-is | **cross-sectional seam** |

**Sequencing rule, pre-registered:** verdicts update priors but never
parameters. A FAIL on B1 lowers the prior of B3 (same family) and raises the
relative value of D2/H1 (different families); it does not license re-tuning
anything. The queue may be re-ordered between phases on evidence; individual
strategies may not be re-cut after their verdict.

---

## 3.7 Batch 2 (Phase 11) — cross-sectional, on the expanded corpus

Phase 11 addressed §3.6's power problem via DATA (NIFTY-500 × 11y, ~3.2×
independent periods, ~4.7× breadth) and the cross-sectional seam, then
pre-registered and implemented 13 fundamentally different strategies
(`research/PREREGISTRATION_BATCH2.md`) — 11 cross-sectional rank strategies + a
calendar and a stage-analysis strategy. Six roadmap items were dropped as
rejected-idea variants (200-DMA, VCP, NR7, Darvas/Turtle, Elder, D3=hvol).
Measured under the frozen D-031 gate on the expanded corpus. Results and the
resulting priors update: see `docs/DECISIONS.md` D-032 and `docs/LEARNINGS.md`.

## 3.6 Post-benchmark priors update (Phase 10, D-031/L-011)

The benchmark amendment re-judged everything: **all 14 FAIL; no strategy shows
an established selection edge.** The batch-1 "survivors" (hvol, wyckoff_spring,
triple_screen) have positive selection POINTS but difference-CI lower bounds
deep in negative territory — indistinguishable from a random entry on 3.5y of
multi-week data. This SUPERSEDES the §3.5 priors below, which were built on the
L-010 point estimates now shown to be within noise.

**What this changes for the queue:**
- **No family has demonstrated edge.** The apparent hvol/spring advantage did
  not survive an honest interval, so their raised priors (§3.5) are withdrawn.
- **The binding problem is now statistical power, not strategy choice.** On 3.5y
  at multi-week horizons the gate cannot resolve a ~60 bps edge from zero. Two
  responses dominate ANY next strategy in expected value:
  1. **The paired date-matched selection gate** (D-031 limitation) — cancels
     drift per-observation, far more power, and is the correct cross-sectional
     alpha measure. Owner-approved amendment, before batch 2.
  2. **More independent history** — the corpus grows daily; a 12-month-horizon
     study needs years, not months, of new data to gain independent blocks.
- **Data acquisitions still lead on EV**: an earnings calendar (PEAD, the one
  event family) and a sector map (rotation) unblock genuinely different
  hypotheses whose edge, if real, is larger per trade and thus easier to resolve
  than the marginal technical edges batch 1 chased.

Batch 2 should not proceed until the power problem is addressed; implementing
more technical variants now would only generate more within-noise FAILs.

## 3.5 Post-batch-1 priors update (Phase 9, D-030/L-010) — SUPERSEDED by §3.6

Queue slots #1–#8 are now measured. What the batch actually taught (selection
edge = gross − random baseline, the only informative statistic at these
horizons per L-010):

- **Attention/volume (hvol, +91 bps at 20d)** and **trapped-liquidity price
  action (wyckoff_spring, +67 bps)** are the two families whose selection edge
  clears cost — the only genuinely interesting results. Raised priors.
- **Pullback continuation (triple_screen, +22)** — positive but below cost.
- **Breakout (donchian, ≈0)** and **own-trend momentum (tsmom, −146!)** —
  no selection edge; tsmom's entries were WORSE than random. Lowered priors
  for B2/B3-variants and A5; A1 (cross-sectional momentum) is a different
  claim and keeps its literature prior, but expectations are tempered.
- **Compression (squeeze, −202)** — actively adverse selection; C1 VCP's
  prior drops sharply. **Anchoring-threshold (hi52, −227)** — the per-symbol
  threshold form is dead; only the published cross-sectional RANK form (via
  the seam) remains worth testing.
- **NOTHING advances to paper trading**: the gate amendment (D-030) precedes
  any further batch. Sequencing for Phase 10: amend gate → re-judge recorded
  measurements → survivor deep-validation (walk-forward, regimes) → THEN the
  cross-sectional seam and batch 2.

## 4. Recommended first implementation (unchanged, re-affirmed post-D-028)

**B1 — time-series (absolute) momentum**, exactly as specified in the library
§6: 12-month formation, 1-month skip, long-only delivery, daily bars, horizon
pre-registered in `meta` BEFORE measurement.

Post-D-028 framing, stated before any code exists: with ~40 independent 20-day
blocks in the corpus, the D-028 CI at a 20-day horizon will be wide, and the
likeliest verdicts are FAIL or BORDERLINE. The deliverable that matters is the
**measured gross edge at multi-week horizons** — the first such number this
platform will have produced — and it decides whether the entire long-horizon
program continues or the library's priors need rewriting.
