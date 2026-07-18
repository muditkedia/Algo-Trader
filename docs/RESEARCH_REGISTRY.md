# Research Registry (PERMANENT)

_Established Phase 13, 2026-07-18. The single source of truth for every research
idea, past and future. **No idea may be implemented before it exists here as an
entry** (`RESEARCH_STANDARDS.md` §1.1). Entries are appended and updated, never
deleted — rejected ideas are archived so they are not silently re-proposed
(§1.7). Priority scores use the Hypothesis Quality Framework
(`RESEARCH_STANDARDS.md` §2); a score ranks research ORDER, it never predicts a
PASS._

## Entry schema (Part A)

Every entry carries: **ID · Title · Category · Rationale (economic/behavioural) ·
Source of edge · Assumptions · Holding period · Market regime · Required datasets
· Measurable prediction · Falsification criteria · Expected statistical power ·
Implementation complexity · Expected research value · Dependencies · Status.**

**Status lifecycle:** `Idea → Prioritised → Ready → Implemented → {Rejected |
Archived}`. `Ready` = pre-registered and unblocked. A strategy advances toward
paper/live only on a certified PASS under the frozen gate (§1.6).

---

## Ranked backlog (Part D)

Priority = Hypothesis Quality Framework weighted score (1-5). "P(new finding)" is
the qualitative probability the work yields a genuinely new, reliable result
(NOT the probability of a PASS).

| Rank | ID | Title | Status | Priority | Info gain | Effort | P(new finding) |
|---|---|---|---|---|---|---|---|
| — | R-001 | Short-horizon event & microstructure | **Archived** (Phase 14) | 4.24 | delivered | — | refuted for certification; expiry lead → R-002 |
| **1** | R-002 | Portfolio-level factor evaluation | Prioritised | **4.28** | High | Med (new mode) | High |
| 2 | R-003 | Event-driven: earnings / PEAD | Idea | 4.08 | High | High (data) | Med-High |
| 4 | R-004 | Index inclusion / deletion flows | Idea | 3.76 | Med-High | High (data) | Med |
| 5 | R-010 | Sector-relative effects | Idea | 3.64 | Med | Med | Med |
| 6 | R-005 | Multi-factor ensembles | Idea | 3.48 | Med | Low | Med |
| 6 | R-008 | Relative-volume / order-flow | Idea | 3.48 | Med | Low-Med | Med |
| 8 | R-006 | Regime switching / conditional exposure | Idea | 3.40 | Low-Med | Med | Low-Med |
| 9 | R-009 | Inter-market / cross-asset signals | Idea | 3.20 | Med | High (data) | Low-Med |
| 10 | R-007 | Market-breadth allocation | Idea | 3.12 | Low-Med | Med | Low |
| 11 | R-011 | Macro-sensitive strategies | Idea | 2.96 | Low-Med | High (data) | Low |

**R-001 and R-002 are effectively tied at the top.** R-001 (short-horizon) is the
pragmatic FIRST move: highest-value work executable **with zero new
infrastructure** under the frozen gate, following the one near-positive result
(tom). R-002 (portfolio-mode) scores marginally higher on quality but **requires
owner sign-off on a new evaluation MODE** (a portfolio-return CI, distinct from
and additional to the per-trade selection-edge gate — it does NOT alter the
frozen gate). Recommend R-001 next; R-002 as the parallel owner decision.

---

## Active entries (Part C)

### R-001 — Short-horizon event & microstructure effects · **ARCHIVED** (Phase 14)
- **Category:** microstructure / calendar / short-horizon
- **Rationale:** the only near-certification in 27 strategies (tom, 3-7d, CI-low
  −7.9) came from a short horizon with many near-independent events.
- **Executed** (Phase 14, `research/PREREGISTRATION_R001.md`): 3 pre-registered
  independent families on NIFTY-500, frozen gate. **All FAIL** — but the
  prediction ("≥1 clears the selection CI above cost") was **refuted**, informatively:
  - **F3 expiry (F&O expiry-week drift): the best short-horizon result in the
    project** — selection +74 bps point (clears the 30.9 cost), beats random
    (rel-PF 1.35), 54,000 signals — yet CI-low −1.6 fails to establish even a
    positive edge at 95%. Directionally supported, NOT certified.
  - F2 gap-fade: FALSIFIED with a clean negative sign (−45 pt / −167) — NSE
    down-gaps CONTINUE, they do not fade (with egap's failed up-gap continuation,
    gaps show downward continuation, no mean-reversion).
  - F1 month-start: rejected (+18/−21) — the turn-of-month edge lives in the
    pre-month-END anticipation (tom), not month-START inflow arrival.
- **Finding:** even at MAXIMUM power (54k signals, short horizon), a per-trade
  short-horizon selection edge does not certify on NSE. The short-horizon
  per-trade thesis is answered. **The directionally-real expiry effect is carried
  forward to R-002 (portfolio mode) as its priority test case** — NOT re-tested
  as more per-trade R-001 variants (which would be parameter-chasing).
- **Status: Archived.** Evidence in the DB (r001_expiry/gap_fade/month_start) and
  `user_data/backtest_results/reports/r001_league.md`.

### R-002 — Portfolio-level factor evaluation · Prioritised
- **Category:** factor investing / measurement mode
- **Rationale:** 12 of 13 cross-sectional factors are directionally real but
  per-trade-noisy (L-012). A monthly-rebalanced decile PORTFOLIO turns the
  cross-section into one return series with ~130 near-independent monthly
  observations — potentially certifiable where the per-trade view is not.
- **Source of edge:** the documented factor premia (momentum, beta, liquidity),
  captured as a diversified portfolio rather than a per-name bet.
- **Assumptions:** the factor edge survives at the portfolio level net of
  rebalancing cost; monthly returns are more nearly independent than overlapping
  per-trade windows.
- **Holding period:** monthly rebalance · **Regime:** all
- **Datasets:** current store (have it).
- **Measurable prediction:** a factor portfolio's monthly excess-return CI (vs an
  equal-weight universe benchmark) excludes zero for ≥1 factor.
- **Falsification:** no factor portfolio's monthly-return CI clears its benchmark.
- **Power:** HIGH (monthly independent obs). **Complexity:** MEDIUM — needs a
  portfolio-backtest + its own CI (a NEW evaluation mode; does NOT touch the
  frozen per-trade gate). **Value:** HIGH — the one route that could certify the
  directionally-real factors. **Dependencies:** owner approval of the new mode.
  **Priority 4.28.**
- **Priority test cases (Phase 14 evidence):** the R-001 **expiry** effect
  (per-trade selection +74 bps point, beats random, but per-trade CI-low −1.6) is
  a near-ideal candidate — a diversified monthly expiry-window portfolio has ~130
  near-independent monthly returns that may certify where the per-trade view
  cannot. Then the directionally-strongest batch-2 factors (illiq, bab, hi52rank,
  xsmom).

### R-003 — Event-driven: earnings / PEAD · Idea (blocked on data)
- **Category:** event-driven · **Rationale:** PEAD is the most-replicated anomaly
  in the literature and is UNTESTED here; the price-only proxy (egap) failed,
  which falsifies the proxy, not the anomaly.
- **Source of edge:** under-reaction to earnings surprises → drift.
- **Holding:** 20–60 days · **Regime:** all · **Datasets:** earnings calendar +
  actual/estimate feed (NOT available).
- **Measurable prediction:** stocks in the top earnings-surprise quantile have a
  selection edge clearing cost over 20-60d. **Falsification:** surprise-sorted
  drift CI-low ≤ cost.
- **Power:** HIGH (event windows). **Complexity:** HIGH (data acquisition).
  **Value:** HIGH. **Dependencies:** earnings dataset. **Priority 4.08.**

### R-004 — Index inclusion / deletion flows · Idea (blocked on data)
- **Category:** event-driven / flows · **Rationale:** index funds must buy
  additions / sell deletions — predictable, price-insensitive demand (Shleifer
  1986). **Source of edge:** forced flow around effective dates.
- **Holding:** days around the event · **Regime:** all · **Datasets:** index
  membership-change calendar (NOT available).
- **Prediction:** additions out-select the universe in the pre-effective window.
  **Falsification:** no selection edge around events.
- **Power:** MED-HIGH (short-horizon but few events/yr). **Complexity:** HIGH
  (data). **Value:** MED-HIGH. **Dependencies:** index-events dataset.
  **Priority 3.76.**

### R-005 — Multi-factor ensembles · Idea
- **Category:** factor combination · **Rationale:** single factors are
  directionally real but noisy; combining ORTHOGONAL ones (momentum × beta ×
  liquidity — never two momenta) is standard practice; `combo` tried only two and
  one was inverted. **Source of edge:** diversification across imperfectly-
  correlated premia.
- **Holding:** weeks-months · **Datasets:** current store.
- **Prediction:** an equal-weighted composite of ≥3 orthogonal directional
  factors has a tighter/positive selection edge than any single leg.
  **Falsification:** the composite is no better than its best leg.
- **Power:** LOW at long horizon (inherits the factor CIs) unless paired with
  R-002. **Complexity:** LOW (framework `composite_percentile`). **Value:** MED.
  **Dependencies:** best done after/with R-002. **Priority 3.48.**

### R-006 — Regime switching / conditional exposure · Idea
- **Category:** regime · **Rationale:** breadth_regime hinted that conditioning
  entries on a market regime matters; conditioning WHICH factor is active by
  regime is untested. **Source of edge:** factor premia are regime-dependent.
- **Holding:** weeks-months · **Datasets:** breadth/market series (built).
- **Prediction:** a regime-conditioned factor out-selects the unconditional one.
  **Falsification:** conditioning adds no selection edge.
- **Power:** LOW (regimes are autocorrelated → few independent regime periods).
  **Complexity:** MED. **Value:** LOW-MED. **Priority 3.40.**

### R-007 — Market-breadth allocation · Idea
- **Category:** market internals · **Rationale:** breadth is built but used only
  as a filter; as an ALLOCATOR (scaling exposure with breadth) it is untested.
- **Holding:** weeks · **Datasets:** breadth (built).
- **Prediction:** breadth-scaled exposure improves risk-adjusted selection.
  **Falsification:** no improvement vs constant exposure.
- **Power:** LOW (autocorrelated). **Complexity:** MED. **Value:** LOW-MED.
  **Priority 3.12.**

### R-008 — Relative-volume / order-flow microstructure · Idea
- **Category:** volume microstructure · **Rationale:** hvol (crude relative
  volume) was directionally positive; finer volume/flow structure at short
  horizons is untested and short-horizon = powered. **Source of edge:**
  attention/flow shocks.
- **Holding:** 5–20 days · **Datasets:** daily volume (have); intraday for finer
  work (NIFTY-100 only).
- **Prediction:** a relative-volume signal has a short-horizon selection edge
  clearing cost. **Falsification:** CI-low ≤ cost.
- **Power:** MED-HIGH (short horizon). **Complexity:** LOW-MED. **Value:** MED.
  **Dependencies:** overlaps R-001 — fold in. **Priority 3.48.**

### R-009 — Inter-market / cross-asset signals · Idea (blocked on data)
- **Category:** cross-asset · **Rationale:** NSE vs global indices / INR / rates /
  commodities may carry orthogonal signal; entirely untested. **Source of edge:**
  lead-lag and risk-on/off transmission.
- **Datasets:** external feeds (NOT available). **Power:** MED. **Complexity:**
  HIGH. **Value:** MED (orthogonality). **Priority 3.20.**

### R-010 — Sector-relative effects · Idea
- **Category:** sector · **Rationale:** sector-neutral ranking and sector rotation
  are untested; **the sector map already exists** (the NIFTY-500 snapshot's
  Industry column, currently un-ingested). **Source of edge:** intra-sector
  relative strength; sector momentum.
- **Holding:** weeks-months · **Datasets:** ingest sector map from the committed
  CSV (in hand). **Power:** MED. **Complexity:** MED (ingest sector + sector-
  relative metric — a cross-sectional component). **Value:** MED.
  **Dependencies:** populate `instruments.sector`. **Priority 3.64.**

### R-011 — Macro-sensitive strategies · Idea (blocked on data)
- **Category:** macro · **Rationale:** rate/INR/liquidity sensitivity untested.
  **Datasets:** macro feeds (NOT available). **Power:** LOW (slow, few regimes).
  **Complexity:** HIGH. **Value:** LOW-MED. **Priority 2.96.**

---

## Archived — explored & rejected (the map of what does not work)

Recorded so they are never re-proposed. All FAILED the frozen gate (D-026, D-030,
D-031, D-032; evidence in the DB and batch reports). Status `Archived`.

| Family | Strategies | Verdict / evidence |
|---|---|---|
| Per-symbol momentum / trend | tsmom, ema200, vwap, hi52 | No selection edge (level/own-trend ≈ random). **Do not re-test per-symbol trend.** |
| Cross-sectional momentum / RS | xsmom, resmom, dualmom, hi52rank | Directionally REAL (+112..+252 pt) but uncertifiable at 21-63d. Re-open only via R-002 (portfolio mode). |
| Breakouts | orb, donchian55 | ORB cost-dead; donchian CI unpowerable. |
| Stage / structural breakout | stage2 | Largest-but-one point edge (+383), CI −883. Re-open only short-horizon or R-002. |
| Volatility compression | nr7, squeeze | Weak/negative; compression predicts a move not its sign. |
| Volatility expansion | volexp_1h | Cost-dead intraday. |
| Low-volatility factor | lowvol | **INVERTED on this sample** — do not re-test as-is. |
| Mean reversion | pullback, wyckoff_spring, xsrev | Cost-hostile; some direction, no certification. |
| Volume / liquidity | hvol, illiq | Directionally real (illiq largest pt edge +392) but noisiest; needs a better cost model before any claim. Short-horizon volume → R-008. |
| Behavioural (lottery) | maxret | Weak; long-horizon. |
| Regime/breadth (as filter) | breadth_regime | Breakout leg's edge, unpowerable; breadth as allocator → R-007. |
| Factor composite | combo | Diluted (one inverted leg). Re-open via R-005 with orthogonal legs. |
| Calendar | tom | **Closest to certification** — promoted into R-001, not archived. |
| Event proxy (price-only) | egap | Proxy FAILED (negative). Real event-driven → R-003. |
