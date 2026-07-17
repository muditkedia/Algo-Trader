# Candidate Strategy Library

_Phase 8 research report. Compiled 2026-07-17; extended same day (Phase 8.5)
with the practitioner-strategy additions (§3 families B7/H1, B3 expansion) and
the post-D-028/D-029 corrections. No strategy here is implemented._

_See `research/IMPLEMENTATION_ROADMAP.md` for the formal tiering, the
ten-strategy queue, and per-strategy effort/risk/dependency assessments._

This document is a **research menu, not a set of claims**. Nothing below is
asserted to be profitable. Every candidate is a hypothesis to be put through the
identical pipeline (`ResearchEngine.research`) and judged by the pre-registered
bars that rejected all six incumbents. The references explain *why experienced
traders and researchers believe the effect exists*; they are not evidence that it
survives Indian transaction costs in 2026, which is exactly what the platform is
built to find out.

---

## 1. The constraint that orders this entire library

This project has already measured the thing that kills strategies, twice, on two
different asset classes (L-006/L-009 on crypto, D-026 on NSE equities):

> **Transaction cost is roughly fixed per round trip. Edge is not.**

From this repo's own `NseEquityCostModel` (statutory rates, not estimates):

| Product | Round trip | Why |
|---|---|---|
| **Intraday (MIS)** | **12.2 bps** | STT 2.5 bps sell-side only |
| **Delivery (CNC)** | **30.9 bps** | STT 0.1% **both** sides = 20 bps dominates |

The D-007 gate requires gross edge ≥ **2× cost**, judged on the **lower 95%
bound**, not the point estimate:

| Product | Edge needed | AND CI-low must exceed |
|---|---|---|
| Intraday | **24.4 bps** | 12.2 bps |
| Delivery | **61.8 bps** | 30.9 bps |

Because the ~31 bps delivery cost is **identical whether you hold for one day or
one year**, the only lever that moves the ratio is the **size of the move you are
trying to capture**:

| Expected move per round trip | Hurdle as a share of the move | Verdict |
|---|---|---|
| 0.3% (intraday scalp) | ~200% of the move | Arithmetically impossible |
| 1% | 62% | Hopeless |
| 3% | 21% | Very hard |
| **6%** | **10%** | Plausible |
| **12%** | **5%** | Comfortable |
| 25% | 2.5% | Cost is a rounding error |

**This is why the library below is deliberately biased toward multi-week and
multi-month holds.** It is not a stylistic preference. It is the only structural
attack on the constraint the project has already measured twice — and it is
D-007's own Option 2 ("re-scope holding horizons far beyond the frozen band — the
edge grows with horizon") and L-006's own conclusion ("trade far less often for
far larger moves").

### What the measurement already told us

| Category | Measured gross edge | Hurdle | Gap |
|---|---|---|---|
| Intraday (4 strategies) | 0.2 – 9.1 bps | 24.4 bps | **3× – 100× short** |
| Daily, 8-bar horizon (ema200) | 59.4 bps | 61.8 bps | **4% short** (but CI-low 23.4 < 30.9) |

The intraday failures are not a tuning gap; they are a category death. The daily
result is the signal worth reading: **at the longest horizon the platform has
ever measured, edge arrived within 4% of the bar.** The horizon was capped at 8
bars — not because anyone chose 8 for ema200_daily, but because 8 was hardcoded
platform-wide (fixed in Phase 8; see §5).

---

## 2. What this platform can and cannot measure today

Verified against the code and the store, not assumed. This is the difference
between a candidate that can be researched next week and one that cannot.

| Constraint | Reality | Consequence |
|---|---|---|
| **Strategy interface** | `entry_signal(dataframe) -> Series` is **per symbol** | **Cross-sectional ranking cannot be expressed.** No strategy can see its peers. |
| **Sector data** | `instruments.sector` is **0/99 populated** | Sector rotation is **blocked** on data, not on logic. |
| **Fundamentals / earnings dates** | Not available (SmartAPI scrip master = symbol/token/name) | PEAD, quality, value are **blocked**. |
| **Index membership history** | Not available | Index inclusion/deletion is **blocked**. |
| **Direction** | **Long-only** | Pairs trading and market-neutral factor spreads are **blocked**. |
| **History depth** | 2023-01-01 → 2026-07-16, **877 daily bars** (98/99 symbols) | ~3.5 years. See the sample-size warning below. |
| **Data defect** | ~~RELIANCE/TCS 381 bars~~ **RESOLVED (D-029)**: root cause was the forward-only incremental downloader after a narrow smoke-test seed; pipeline fixed (head-gap backfill), both symbols repaired to full 877-bar coverage | Only JIOFIN starts late (2023-08-21), which is genuine — it listed then. |

### The sample-size problem is the real limit on long-horizon research

Long holds buy cost survivability and pay for it in statistical power:

| Hold | Non-overlapping windows per symbol (3.5y) | Across 96 symbols |
|---|---|---|
| 8 days | ~110 | ~10,500 |
| 1 month | ~42 | ~4,000 |
| 3 months | ~14 | ~1,344 |
| 12 months | ~3.5 | ~336 |

Those cross-symbol counts flatter badly: all 96 symbols share **one** market
history. A 12-month-momentum study on 3.5 years of NSE data has roughly **three
independent time periods**, not 336 observations. It cannot distinguish a real
effect from one bull market.

**Two honest consequences:**

1. **A long-horizon PASS on this corpus is weak evidence.** It should be treated
   as "not yet rejected", not "validated". The 30-signal `MIN_TRADES` floor was
   designed for intraday sample sizes and does not protect against this.
2. ~~The CI method needs extending~~ **DONE (D-028).** The CI now resamples
   contiguous blocks of days at least as long as each horizon's forward window,
   reusing `monte_carlo.py`'s stationary bootstrap; horizons inside one day keep
   the L-009 day resample bit-for-bit. Verified on the real corpus: all six
   verdicts identical, 15m CI-lows bit-identical, and — the finding that
   justifies the whole exercise — **ema200_daily's CI-low fell from 23.4 to
   6.2 bps** and nr7_daily's from 4.0 to −13.0 bps once the 8-day overlap was
   honestly accounted for. The old day-resampled CI was overstating long-horizon
   confidence by ~17 bps. Every long-horizon claim in this document should be
   read against the CORRECTED gate: it is meaningfully harder than the D-026
   table suggested, and that is the right direction for a gate to be wrong in.

### Multiple testing: the risk this phase itself creates

Phase 8's objective is to evaluate *many* ideas quickly. That is also how false
discoveries are manufactured. Testing 25 candidates against a 95%-confidence
bound yields **~1.25 expected false PASSes from noise alone**, and the pipeline
compounds it by selecting the **best of several horizons** per strategy
(`EdgeReport.best()` takes `max(gross_mean)`) without adjusting for that choice.

This is not hypothetical — it is the central finding of the literature on exactly
this activity (Harvey, Liu & Zhu 2016; Bailey & López de Prado 2014; Aronson
2006). Their prescription is a higher bar for later candidates, not the same bar
applied more times.

**Recommendation (owner decision, not taken):** keep a count of every candidate
ever measured — the evidence DB already has it, since every strategy is
registered — and either raise the significance bar as the count grows, or accept
that the Nth PASS is materially weaker evidence than the first. Do not let a
25-candidate sweep quietly report a 5%-significance verdict.

---

## 3. The candidate library

**Priority** = implement order recommendation. **Cost survivability** is a
qualitative judgement derived from §1's arithmetic (hurdle ÷ expected move) and
the documented hypothesis — **never from advertised win rates**.

### Family A — Cross-sectional momentum & relative strength

> The single best-documented anomaly in the equity literature, and the family
> whose hold periods make the cost hurdle small. **Blocked on a per-symbol
> interface** (§2) — see §5 for the minimal extension.

#### A1. Cross-sectional momentum (12-1)
- **Hypothesis:** stocks that outperformed over the past 12 months (skipping the
  most recent month, to avoid short-term reversal) continue to outperform over
  the next 1–3 months. Attributed to under-reaction to information and to
  delayed diffusion of news across investors.
- **Holding:** 1–3 months · **Timeframe:** 1d (monthly rebalance) · **Regime:**
  trending/bull; crashes at sharp reversals
- **Strengths:** the most replicated cross-sectional effect in finance; survives
  across markets, asset classes and decades; **documented specifically for India**
- **Weaknesses:** **momentum crashes** — severe, fast drawdowns when a bear
  market rebounds (Daniel & Moskowitz); crowded; long formation window burns
  scarce history
- **Indicators:** 12-month return, 1-month skip, cross-sectional rank
- **Cost sensitivity:** **LOW** — 3-month holds on ~10–20% dispersion → hurdle is
  3–6% of the move
- **Complexity:** Medium (needs the cross-sectional seam)
- **Priority:** **1**
- **References:** Jegadeesh & Titman (1993) "Returns to Buying Winners and
  Selling Losers", *Journal of Finance*; Carhart (1997) four-factor model;
  Asness, Moskowitz & Pedersen (2013) "Value and Momentum Everywhere", *JF*;
  **Agarwalla, Jacob & Varma (IIM Ahmedabad), "Four factor model in Indian
  equities market" — the IIMA Financial Markets data library publishes
  Fama-French + momentum factors for India**; Quantpedia

#### A2. Residual (idiosyncratic) momentum
- **Hypothesis:** momentum measured on returns *residual* to market/factor
  exposure isolates stock-specific under-reaction and strips the factor bets that
  cause momentum crashes.
- **Holding:** 1–3 months · **Timeframe:** 1d · **Regime:** most; less
  regime-fragile than A1
- **Strengths:** historically better risk-adjusted return than plain momentum;
  materially smaller crash exposure
- **Weaknesses:** needs a factor/market model → more machinery and more
  researcher choices (each an overfitting surface); harder to attribute failure
- **Indicators:** rolling regression vs the index, residual cumulative return
- **Cost sensitivity:** **LOW**
- **Complexity:** High
- **Priority:** 6
- **References:** Blitz, Huij & Martens (2011) "Residual Momentum"; Gutierrez &
  Prior; Quantpedia

#### A3. 52-week-high proximity
- **Hypothesis:** proximity to the 52-week high predicts continuation. Traders
  anchor on the 52-week high and under-react to good news near it — the anchor
  acts as a psychological resistance that, once cleared, releases the move.
- **Holding:** 1–6 months · **Timeframe:** 1d · **Regime:** bull
- **Strengths:** a *single, parameter-free* measure (price ÷ 52w high) — almost
  nothing to overfit, which is rare and valuable; George & Hwang found it
  dominates conventional momentum
- **Weaknesses:** concentrates into whatever ran hardest; fails at market tops
- **Indicators:** 52-week high, ratio to close
- **Cost sensitivity:** **LOW**
- **Complexity:** **Low** (per-symbol as a *threshold*; cross-sectional as a rank)
- **Priority:** **2**
- **References:** George & Hwang (2004) "The 52-Week High and Momentum
  Investing", *Journal of Finance*; O'Neil (*How to Make Money in Stocks*);
  Quantpedia

#### A4. Sector / industry rotation
- **Hypothesis:** industry membership drives a large share of momentum; rotating
  into leading sectors captures it with less single-stock risk.
- **Status:** **BLOCKED — no sector data** (`instruments.sector` 0/99). Needs a
  sector map sourced and ingested first. This is a data task, not a strategy task.
- **Cost sensitivity:** LOW · **Priority:** deferred
- **References:** Moskowitz & Grinblatt (1999) "Do Industries Explain
  Momentum?", *JF*

#### A5. Dual momentum (relative + absolute)
- **Hypothesis:** combine cross-sectional strength (buy the leaders) with an
  absolute filter (only when the asset's own trend is positive), so the strategy
  steps aside in bear markets — the regime where A1 crashes.
- **Holding:** 1–3 months · **Timeframe:** 1d
- **Strengths:** directly targets momentum's worst failure mode; simple
- **Weaknesses:** the absolute filter whipsaws in choppy markets; popularized in
  a trade book — treat published results as marketing until measured here
- **Cost sensitivity:** LOW · **Complexity:** Medium · **Priority:** 7
- **References:** Antonacci (2014) *Dual Momentum Investing*; Moskowitz, Ooi &
  Pedersen (2012)

---

### Family B — Time-series trend (per-symbol — implementable **today**)

> Same cost logic as Family A, but expressible on the **current interface with no
> platform change**. This is why the first implementation should come from here.

#### B1. Absolute / time-series momentum
- **Hypothesis:** a stock's own past 12-month return predicts its next-month
  return. Distinct from A1: it is a statement about the asset's own trend
  persistence, not its rank among peers.
- **Holding:** 1–3 months · **Timeframe:** 1d · **Regime:** trending
- **Strengths:** documented across ~60 assets and 25+ years and multiple asset
  classes; **needs no cross-sectional seam**; almost no parameters
- **Weaknesses:** long formation eats scarce history (3.5y ⇒ ~2.5y usable);
  whipsaws at turning points; 3.5 years contains ~3 independent 12-month periods
- **Indicators:** 12-month return, 1-month skip
- **Cost sensitivity:** **LOW** · **Complexity:** **Low** · **Priority:** **3**
- **References:** Moskowitz, Ooi & Pedersen (2012) "Time Series Momentum",
  *Journal of Financial Economics*; Asness/Moskowitz/Pedersen (2013)

#### B2. Long-horizon moving-average trend (200-day / 10-month)
- **Hypothesis:** price above a long-term moving average identifies a regime with
  better risk-adjusted returns; the MA acts as a slow, objective trend filter.
- **Holding:** months · **Timeframe:** 1d · **Regime:** bull
- **Strengths:** the most widely used trend rule in existence; extremely simple;
  studied for decades
- **Weaknesses:** **this is the closest relative of the FAILED `ema200_daily`**
  (which measured 59.4 bps at an 8-bar cap). Re-testing it at a longer horizon is
  legitimate **only as a new, separately pre-registered candidate** — changing
  ema200_daily's horizon after its FAIL is precisely the tuning D-026 forbids.
  Also: Sullivan/Timmermann/White showed the classic MA results **do not survive
  a data-snooping adjustment**.
- **Cost sensitivity:** **LOW–MEDIUM** · **Complexity:** **Low** · **Priority:** 5
- **References:** Brock, Lakonishok & LeBaron (1992) "Simple Technical Trading
  Rules...", *JF*; **Sullivan, Timmermann & White (1999)** (the rebuttal — cite
  both); Faber (2007) "A Quantitative Approach to Tactical Asset Allocation";
  Zakamulin, *Market Timing with Moving Averages*

#### B3. Donchian / Darvas / Turtle channel breakout
- **Hypothesis:** a breakout to a new N-week high signals a genuine supply/demand
  imbalance; large trends begin at new highs, not at bottoms.
- **Holding:** weeks–months (trend-dependent) · **Timeframe:** 1d
- **Strengths:** the canonical trend-following entry (Turtles); objective;
  fat-tailed payoff that suits a fixed cost
- **Weaknesses:** low win rate by construction (~30–40%) — the whole return lives
  in the tail, so it needs a large sample to judge, which 3.5y does not give;
  false breakouts in range markets
- **Indicators:** rolling N-day high, ATR
- **Objectively codable variants** (Part C practitioner audit — every rule below
  is fully mechanical):
  - *Plain Donchian*: entry = close above the prior N-day high (e.g. N=55).
    One parameter. The purest expression; **the variant to measure first.**
  - *Turtle System 2*: 55-day breakout entry; the original 2N (2×ATR) stop and
    risk-fraction sizing are **already the platform's own risk engine** — what
    remains distinct is only the entry. System 1's 20-day entry with the
    "skip-if-last-breakout-won" filter is codable but path-dependent (state per
    symbol), and its documented purpose was whipsaw reduction — a refinement to
    test only if the plain entry shows edge. **Pyramiding (units) is NOT
    integrable** — the portfolio manager opens single positions; adding scale-in
    would be a platform change, out of scope.
  - *Darvas box*: box top = a new high not exceeded for 3 consecutive days; box
    bottom symmetric on the low; entry = close above the box top on
    above-average volume. Fully mechanical (4 parameters), but more parameters
    than plain Donchian for the same thesis — measure only if Donchian survives.
- **Cost sensitivity:** **LOW** · **Complexity:** **Low** · **Priority:** 4
- **References:** Darvas (1960) *How I Made $2,000,000 in the Stock Market*;
  Covel, *The Complete TurtleTrader*; Faith, *Way of the Turtle* (the original
  rules, published); Kaufman, *Trading Systems and Methods*; Clenow, *Stocks on
  the Move*

#### B4. Stage-2 breakout (Weinstein)
- **Hypothesis:** stocks move through four stages; buying the Stage 1→2
  transition (breakout from a base above a rising 30-week MA on volume expansion)
  catches the markup phase.
- **Holding:** months · **Timeframe:** 1d/1w · **Regime:** bull
- **Strengths:** a coherent structural thesis rather than an indicator artifact;
  heavily used by discretionary professionals; the volume requirement is a real
  participation filter
- **Weaknesses:** "base" and "stage" are **discretionary concepts** — encoding
  them introduces many free parameters, each an overfitting surface; the honest
  risk is that the code tests the encoding, not the idea
- **Cost sensitivity:** LOW · **Complexity:** High · **Priority:** 9
- **References:** Weinstein (1988) *Secrets for Profiting in Bull and Bear
  Markets*; O'Neil (CANSLIM)

#### B5. Trend + volatility-parity sizing
- **Hypothesis:** not an entry at all — a *sizing* overlay that equalizes risk
  contribution across positions so no single volatile name dominates the outcome.
- **Note:** the platform **already does this** (`risk_based_stake`, D-022/D-024).
  Listed for completeness: it is not a candidate, it is the existing engine.
- **Priority:** n/a
- **References:** Clenow, *Stocks on the Move*; Carver, *Systematic Trading*

#### B7. Elder Triple Screen (weekly tide, daily wave) — added Phase 8.5
- **Hypothesis:** trade only in the direction of the higher-timeframe trend (the
  "tide") and enter on a lower-timeframe pullback against it (the "wave") —
  buying temporary weakness inside an established weekly uptrend combines trend
  persistence with a better entry price than chasing strength.
- **Holding:** days–weeks · **Timeframe:** 1d (weekly screen computed internally)
  · **Regime:** trending
- **Objectively measurable components** (Part C audit):
  - Screen 1 (tide): weekly EMA slope > 0, or weekly MACD-histogram rising —
    both mechanical on resampled weekly bars.
  - Screen 2 (wave): daily 2-day Force Index < 0, or daily Stochastic below a
    threshold — mechanical.
  - Screen 3 (trigger): close above the prior day's high — mechanical.
  - Elder's money-management and stop rules are superseded by the platform's
    own risk engine (D-006/D-023), exactly as with the Turtle rules.
- **Integration:** a single `1d` strategy; `prepare()` resamples daily bars to
  weekly internally. **The lookahead trap is the weekly bar**: only COMPLETED
  weeks may feed screen 1 (shift the weekly value to the following week), or the
  Friday close leaks into Monday–Thursday signals. This must be a test.
- **Strengths:** the multi-timeframe-confirmation idea has survived in
  practitioner use for 30+ years; each component is simple and separately
  auditable.
- **Weaknesses:** a trade-book strategy with thin peer-reviewed support; it is
  a *pullback* thesis, and the platform's only measured pullback strategy
  (pullback_15m) was its worst FAIL — the defence is that at daily/weekly scale
  the target move is 10–50x larger against the same cost, which is exactly the
  cost-arithmetic difference this library is organized around.
- **Cost sensitivity:** **MEDIUM** (3–8% target moves) · **Complexity:** Medium
- **Priority:** roadmap queue #8
- **References:** Elder (1993) *Trading for a Living*; Elder (2002) *Come Into
  My Trading Room*; on the underlying trend-persistence leg, Moskowitz, Ooi &
  Pedersen (2012)

---

### Family C — Volatility compression → expansion

#### C1. Volatility Contraction Pattern (VCP)
- **Hypothesis:** a sequence of progressively tighter pullbacks on declining
  volume shows supply being absorbed; the breakout from the final contraction
  starts a large move. Compression is the *setup*; expansion is the trade.
- **Holding:** weeks–months · **Timeframe:** 1d · **Regime:** bull
- **Strengths:** volatility clustering (compression→expansion) is one of the most
  robust statistical facts about returns — the *mechanism* is real even if the
  *pattern encoding* is arguable; targets large moves, so cost is small
- **Weaknesses:** the pattern is discretionary; **volatility expansion is
  directionless** — compression reliably predicts a big move, **not its sign**.
  That is the crux, and `volexp_1h` already FAILED (PF 0.69) on exactly this
  ambiguity at an intraday horizon.
- **Indicators:** ATR ratio, range contraction, volume trend, pivot high
- **Cost sensitivity:** **LOW–MEDIUM** · **Complexity:** High · **Priority:** 8
- **References:** Minervini (2013) *Trade Like a Stock Market Wizard*; Crabel
  (1990) *Day Trading with Short Term Price Patterns and Opening Range Breakout*;
  Bollinger, *Bollinger on Bollinger Bands*

#### C2. Weekly squeeze (Bollinger inside Keltner)
- **Hypothesis:** as C1 with an objective, parameter-light compression measure at
  a weekly scale, so the move being targeted is large enough to clear cost.
- **Strengths:** far more objective than C1 — much less to overfit
- **Weaknesses:** same directional ambiguity as C1
- **Cost sensitivity:** MEDIUM · **Complexity:** Low · **Priority:** 10
- **References:** Bollinger; Carter, *Mastering the Trade*

#### C3. NR7 / inside-day cluster at a multi-week horizon
- **Hypothesis:** Crabel's contraction signal, but held for weeks rather than days.
- **Weaknesses:** **`nr7_daily` already FAILED** (50.1 bps vs a 61.8 bps bar,
  CI-low 4.0). Its edge was *real but insufficient*. A longer-horizon version must
  be a **new pre-registered candidate**, not a re-tuned old one.
- **Cost sensitivity:** MEDIUM · **Priority:** 12
- **References:** Crabel (1990)

---

### Family D — Event & attention

#### D1. Post-Earnings-Announcement Drift (PEAD)
- **Hypothesis:** prices under-react to earnings surprises and drift in the
  surprise's direction for 30–90 days. One of the oldest and most replicated
  anomalies, and a direct violation of semi-strong efficiency.
- **Status:** **BLOCKED — no earnings data.** Needs an earnings calendar +
  actual/estimate feed. This is the **highest-value data acquisition** available
  to the project (see §4).
- **Cost sensitivity:** **LOW** (60-day drift on a surprise ⇒ several % move)
- **References:** Ball & Brown (1968); Bernard & Thomas (1989, 1990); Quantpedia

#### D2. Earnings-gap continuation (price-only PEAD proxy)
- **Hypothesis:** a large gap on huge volume marks an information event; the
  under-reaction that drives PEAD should be visible in *price and volume alone*,
  without knowing the EPS number.
- **Holding:** 20–60 days · **Timeframe:** 1d · **Regime:** most
- **Strengths:** **captures the PEAD hypothesis with data we already have** —
  this is the cheapest route to the best-documented anomaly on the blocked list;
  targets multi-% moves
- **Weaknesses:** a proxy — gaps also come from non-earnings news, blocks and
  index events; without the surprise sign it cannot separate "beat" from "miss
  already priced"
- **Indicators:** overnight gap %, volume ratio, ATR-normalized gap
- **Cost sensitivity:** **LOW–MEDIUM** · **Complexity:** **Low** · **Priority:** **2 (tie)**
- **References:** Bernard & Thomas (1989); Barber & Odean (2008) "All That
  Glitters", *RFS* (attention); Gervais/Kaniel/Mingelgrin (2001)

#### D3. High-volume return premium
- **Hypothesis:** stocks with abnormally high relative volume earn positive
  returns over the following 1–4 weeks — visibility/attention shocks shift the
  investor base.
- **Holding:** 5–20 days · **Timeframe:** 1d
- **Strengths:** published in a top journal; volume is data we already have
- **Weaknesses:** the effect is modest (a few % over weeks) so the hurdle is a
  meaningful share of it; the original work is US, 1990s, and volume dynamics
  have changed with electronic trading
- **Cost sensitivity:** **MEDIUM** · **Complexity:** Low · **Priority:** 11
- **References:** Gervais, Kaniel & Mingelgrin (2001) "The High-Volume Return
  Premium", *Journal of Finance*

#### D4. Gap-and-go
- **Hypothesis:** a stock gapping up on volume continues intraday.
- **Weaknesses:** **intraday ⇒ 24.4 bps hurdle on a fraction-of-a-percent move.**
  D-026 killed four strategies at exactly this frequency. Include only to
  document that the category was considered and rejected on arithmetic.
- **Cost sensitivity:** **VERY LOW survivability** · **Priority:** do not implement

#### D5. Index inclusion / deletion
- **Hypothesis:** index funds must buy additions, creating predictable
  price-insensitive demand — the classic demonstration that demand curves for
  stocks slope down.
- **Status:** **BLOCKED — no index-membership history.**
- **References:** Shleifer (1986) "Do Demand Curves for Stocks Slope Down?", *JF*;
  Harris & Gurel (1986)

---

### Family E — Low risk

#### E1. Low volatility / betting against beta
- **Hypothesis:** low-beta/low-volatility stocks deliver higher **risk-adjusted**
  returns than high-beta ones — the opposite of CAPM. Attributed to leverage
  constraints (investors who cannot use leverage bid up high-beta stocks) and to
  benchmark-relative mandates.
- **Holding:** months–quarters · **Timeframe:** 1d
- **Strengths:** one of the few anomalies with a *structural* (not behavioural)
  explanation, so it is less likely to be arbitraged; low turnover ⇒ tiny cost
  drag; **documented in India**
- **Weaknesses:** it is a **risk-adjusted** claim — raw returns may lag, and this
  platform's gates (PF, expectancy) are raw; underperforms badly in strong bulls;
  needs a cross-sectional rank or an absolute vol threshold
- **Cost sensitivity:** **VERY LOW** (lowest turnover in the library)
- **Complexity:** Medium · **Priority:** **5 (tie)**
- **References:** Ang, Hodrick, Xing & Zhang (2006) "The Cross-Section of
  Volatility and Expected Returns", *JF*; Frazzini & Pedersen (2014) "Betting
  Against Beta", *JFE*; Blitz & van Vliet (2007) "The Volatility Effect", *JPM*;
  Joshipura & Peswani (low-volatility anomaly in India)

#### E2. Low idiosyncratic volatility
- As E1, on residual rather than total volatility.
- **Priority:** 13 · **References:** Ang, Hodrick, Xing & Zhang (2006)

---

### Family F — Mean reversion

#### F1. Short-term reversal (1-week / 1-month)
- **Hypothesis:** short-horizon losers rebound as liquidity-driven price pressure
  unwinds. The reverting party is *paid for providing liquidity*.
- **Holding:** 5–20 days · **Timeframe:** 1d
- **Strengths:** well documented; high signal frequency ⇒ good sample size, which
  this corpus badly needs
- **Weaknesses:** **the decisive one** — reversal profits are concentrated in
  illiquid, high-spread stocks, i.e. the return *is* largely compensation for
  transaction costs. The literature specifically finds reversal profits largely
  **vanish after realistic costs**. On a 1–3% move against a 30.9 bps delivery
  round trip, the hurdle is 20–60% of the move.
- **Cost sensitivity:** **HIGH (bad)** · **Complexity:** Low · **Priority:** 14
- **References:** Jegadeesh (1990); Lehmann (1990); Lo & MacKinlay (1990);
  **Avramov, Chordia & Goyal (2006) "Liquidity and Autocorrelations in Individual
  Stock Returns", *JF*** (the cost rebuttal — the one that matters here)

#### F2. RSI(2) mean reversion
- **Hypothesis:** short-term oversold readings in an uptrend revert.
- **Weaknesses:** popular in trade books, **thin peer-reviewed support**; the
  canonical published results come from the same authors selling the method; a
  textbook data-mining risk. Short holds ⇒ hostile cost arithmetic.
- **Cost sensitivity:** **HIGH (bad)** · **Priority:** 15
- **References:** Connors & Alvarez (2008) *Short Term Trading Strategies That
  Work*; **Aronson (2006) *Evidence-Based Technical Analysis*** (the
  data-snooping counterweight — most such rules fail once bias is accounted for)

#### F3. Long-term reversal (3–5 year)
- **Hypothesis:** extreme 3–5 year losers outperform extreme winners.
- **Status:** **BLOCKED — needs 3–5y formation *plus* a 3–5y hold; the corpus is
  3.5y total.** Not measurable, in principle, on this data.
- **References:** De Bondt & Thaler (1985) "Does the Stock Market Overreact?", *JF*

#### F4. Pairs trading / statistical arbitrage
- **Hypothesis:** cointegrated pairs revert to their spread.
- **Status:** **BLOCKED — long-only platform.** Requires shorting.
- **Weaknesses (for the record):** the original study's profits declined
  substantially in later decades as the trade was arbitraged.
- **References:** Gatev, Goetzmann & Rouwenhorst (2006) "Pairs Trading", *RFS*;
  Avellaneda & Lee (2010)

---

### Family G — Microstructure & seasonality

#### G1. Overnight return premium
- **Hypothesis:** a large share of equity return accrues **overnight** rather than
  intraday; buy at close, sell at open.
- **Cost arithmetic (why this is in the library and not on the shortlist):** the
  premium is on the order of **a few bps per night**, and holding overnight makes
  it a **delivery** trade at **30.9 bps** round trip. The hurdle is ~5–10× the
  effect. **Arithmetically dead** — and worth recording as such, because it is a
  genuinely real effect that this project still cannot trade.
- **Cost sensitivity:** **DEAD** · **Priority:** do not implement
- **References:** Cliff, Cooper & Gulen, "Return Differences between Trading and
  Non-Trading Hours"; Bogousslavsky (2021) "The Cross-Section of Intraday and
  Overnight Returns", *JFE*

#### G2. Turn-of-the-month
- **Hypothesis:** returns concentrate around month boundaries (salary/SIP inflows
  — in India, monthly systematic-investment flows are a large, real, dated
  demand). India has an unusually strong structural version of this mechanism.
- **Holding:** 3–5 days, ~12×/year · **Cost sensitivity:** **MEDIUM–HIGH**
  (a ~1% move vs a 30.9 bps hurdle)
- **Strengths:** a *specific, checkable, India-relevant mechanism* rather than a
  data-mined calendar quirk; trivially simple; near-zero overfitting surface
- **Weaknesses:** calendar effects are the classic data-mining trap and many have
  decayed post-publication; ~42 events over 3.5y is a thin sample
- **Complexity:** **Very low** · **Priority:** 16
- **References:** Ariel (1987); Lakonishok & Smidt (1988); Heston & Sadka (2008)

#### G3. Amihud illiquidity premium
- **Hypothesis:** illiquid stocks earn a premium as compensation for
  illiquidity.
- **Weaknesses:** **self-defeating here.** The premium *is* payment for the
  spread and impact you are about to pay, and this platform's cost model assumes
  a flat 2 bps slippage that would be badly optimistic exactly where the premium
  lives. The universe is NIFTY-100 — by construction the most liquid names — so
  there is little premium to harvest.
- **Cost sensitivity:** **DEAD for this universe** · **Priority:** do not implement
- **References:** Amihud (2002) "Illiquidity and stock returns", *Journal of
  Financial Markets*

### Family H — Price action (added Phase 8.5, Part C)

#### H1. Wyckoff spring / failed-breakdown reclaim
- **Hypothesis:** when price breaks below a well-tested multi-week range low and
  snaps back inside within a few bars, the breakdown trapped sellers (and
  triggered stops) at exactly the level where larger buyers were absorbing
  supply; the failed move marks the range as accumulation and fuels the markup
  out of it. The information is in the *failure* of the move — a genuinely
  different signal source from trend, momentum, or compression.
- **Holding:** 2–8 weeks · **Timeframe:** 1d · **Regime:** range→trend
  transition
- **Objectively measurable components** (Part C audit — Wyckoff's narrative
  concepts reduced to what is mechanical):
  - Range: rolling N-day high/low whose width is below a threshold (e.g. range
    height < k × ATR or < x% of price) for at least M days — mechanical.
  - Spring: a bar whose low breaks the range low, followed within j bars by a
    close back above the range low — mechanical, edge-triggered.
  - Optional confirmation: breakdown-bar volume above average (climactic
    supply), reclaim on rising volume — mechanical.
  - NOT codable and therefore NOT included: Wyckoff's "composite man"
    narrative, phase labelling (A–E), effort-vs-result judgement. Any encoding
    of those would test my encoding, not Wyckoff.
- **Integration:** a pure per-symbol vectorized `1d` entry signal —
  implementable on the current interface today, no platform change.
- **Strengths:** distinct hypothesis (trapped liquidity) from everything else in
  the library; objective core; the target move (the range height plus trend
  resumption, typically 5–15%) makes the cost hurdle 2–6% of the move.
- **Weaknesses:** practitioner evidence only — no peer-reviewed literature
  isolates the spring; pattern statistics compilations (Bulkowski) are
  data-mined and post-hoc; range/spring parameters (N, M, j, k) are an
  overfitting surface that must be fixed BEFORE measurement and never tuned.
- **Cost sensitivity:** **LOW–MEDIUM** · **Complexity:** Medium
- **Priority:** roadmap queue #5
- **References:** Wyckoff (1931) *The Richard D. Wyckoff Method of Trading and
  Investing in Stocks*; Pruden, *The Three Skills of Top Trading*; Grimes, *The
  Art and Science of Technical Analysis* (documents the failure-test as a
  high-expectancy setup — practitioner claim, to be measured, not believed);
  Bulkowski, *Encyclopedia of Chart Patterns* (statistics, with the data-mining
  caveat above)

### Practitioner systems that map onto existing entries (Part C audit)

Recorded so nobody re-adds these as if they were new hypotheses:

- **CAN SLIM (O'Neil):** C/A (earnings growth) and I (institutional sponsorship)
  are **fundamentals — blocked** (no earnings/holder data). N (new high) ≈
  **A3**; L (relative-strength leader) ≈ **A1** (needs the cross-sectional
  seam); the cup-with-handle base breakout ≈ **B4**; M (market direction) needs
  index data or market breadth — breadth is computable but cross-sectional, so
  it waits on the same seam. CAN SLIM's technical core is therefore already in
  the library as A3 + A1 + B4; no new entry.
- **Institutional ORB variants:** intraday-hold ORB is **dead on cost
  arithmetic** (orb_15m: 3.0 bps gross vs 24.4 bps hurdle — D-026). An ORB used
  only as *entry timing* for a multi-day breakout position is a refinement of
  **B3**, worth testing only if B3 itself survives measurement; no new entry.
- **Relative-strength leaders (Minervini/O'Neil screens):** = **A1/A3**; the
  practitioner versions add discretionary chart criteria that are not codable
  without inventing parameters; no new entry.
- **Turtle Trading / Darvas:** folded into **B3** as explicit codable variants
  (see B3). **Weinstein Stage Analysis** is already **B4**. **Minervini VCP**
  is already **C1**.

---

## 4. Summary table

| # | Candidate | Family | Hold | Cost survivability | Implementable now? | Priority |
|---|---|---|---|---|---|---|
| A1 | Cross-sectional momentum 12-1 | momentum | 1–3 mo | **LOW risk** | needs cross-section | **1** |
| A3 | 52-week-high proximity | momentum | 1–6 mo | **LOW risk** | **yes** (threshold form) | **2** |
| D2 | Earnings-gap continuation | event | 20–60 d | LOW–MED | **yes** | **2** |
| B1 | Time-series momentum | trend | 1–3 mo | **LOW risk** | **yes** | **3** |
| B3 | Donchian/Darvas breakout | trend | wks–mo | **LOW risk** | **yes** | 4 |
| B2 | 200-day MA trend | trend | months | LOW–MED | **yes** | 5 |
| E1 | Low volatility / BAB | low-risk | mo–qtr | **VERY LOW risk** | needs cross-section | 5 |
| A2 | Residual momentum | momentum | 1–3 mo | LOW | needs cross-section | 6 |
| A5 | Dual momentum | momentum | 1–3 mo | LOW | needs cross-section | 7 |
| H1 | Wyckoff spring | price action | 2–8 wk | LOW–MED | **yes** | **5 (queue)** |
| C1 | VCP | vol compression | wks–mo | LOW–MED | **yes** | 8 |
| B7 | Elder triple screen | trend+pullback | dys–wks | MED | **yes** | 8 (queue) |
| B4 | Stage-2 breakout | trend | months | LOW | **yes** | 9 |
| C2 | Weekly squeeze | vol compression | wks | MED | **yes** | 10 |
| D3 | High-volume premium | event | 5–20 d | MED | **yes** | 11 |
| C3 | NR7 multi-week | vol compression | wks | MED | **yes** | 12 |
| E2 | Low idiosyncratic vol | low-risk | mo–qtr | VERY LOW risk | needs cross-section | 13 |
| F1 | Short-term reversal | mean reversion | 5–20 d | **HIGH risk** | **yes** | 14 |
| F2 | RSI(2) | mean reversion | 2–5 d | **HIGH risk** | **yes** | 15 |
| G2 | Turn-of-month | seasonal | 3–5 d | MED–HIGH | **yes** | 16 |
| D1 | PEAD | event | 30–90 d | **LOW risk** | **blocked: earnings data** | data first |
| A4 | Sector rotation | rotation | 1–3 mo | LOW | **blocked: sector data** | data first |
| D5 | Index inclusion | event | days | LOW | **blocked: index calendar** | data first |
| F3 | Long-term reversal | mean reversion | 3–5 y | LOW | **blocked: 3.5y history** | never on this corpus |
| F4 | Pairs / stat-arb | mean reversion | days–wks | MED | **blocked: long-only** | needs shorting |
| G1 | Overnight premium | microstructure | overnight | **DEAD** | yes, but pointless | do not implement |
| G3 | Amihud illiquidity | microstructure | mo | **DEAD** (this universe) | no | do not implement |
| D4 | Gap-and-go | intraday | hours | **DEAD** | yes, but pointless | do not implement |
| B5 | Vol-parity sizing | overlay | n/a | n/a | **already built** | n/a |

**29 candidates after the Phase 8.5 practitioner additions (B7, H1): 18
implementable, 5 blocked on data/direction, 3 rejected on cost arithmetic,
1 already built, plus the cross-sectional group (A1/A2/A5/E1/E2) waiting on the
seam.** Turtle/Darvas/Weinstein/VCP/CAN-SLIM/ORB/RS-leaders were audited and
map onto existing entries (see the practitioner-systems note in §3) — they are
deliberately NOT double-counted.

---

## 5. What the pipeline needs before the long-horizon work begins

Phase 8 delivered the horizon seam (`meta.horizon_bars` / `meta.max_hold_bars`),
so a candidate can now declare a multi-month hypothesis and be measured at it.

1. ~~Block-bootstrap CIs~~ **DONE (Phase 8.5, D-028)** — implemented, verified
   verdict-preserving on the real corpus, and already material: the honest
   long-horizon gate is ~17 bps harder than the D-026 table recorded (§2).

2. **A cross-sectional seam** for Family A and E remains the one open platform
   gap — the best-documented, most cost-survivable families cannot be expressed
   on `entry_signal(dataframe) -> Series`. This is an *additive* interface
   method (e.g. an optional `prepare_cross_section(frames) -> frames` the
   engine calls once per sweep before the per-symbol loop), **not** a redesign:
   per-symbol strategies would be untouched. Owner decision; scheduled in the
   roadmap between queue slots #8 and #9.

The seam is not required for queue slots #1–#8, which are deliberately
per-symbol.

---

## 6. Recommended implementation order

Diversity of *hypothesis* is the selection criterion — not variations on one
idea. Each of the first five attacks the cost constraint from a different angle
and would fail for a different reason:

| Order | Candidate | Why this one | Distinct hypothesis |
|---|---|---|---|
| **1** | **B1 Time-series momentum** | Best-documented effect that is **implementable today with no platform change**; near-parameter-free; directly executes D-007 Option 2 | trend persistence |
| **2** | **A3 52-week-high proximity** | Parameter-free (price ÷ 52w high) ⇒ almost nothing to overfit; works as a per-symbol threshold before any cross-sectional work | anchoring / under-reaction |
| **3** | **D2 Earnings-gap continuation** | Cheapest route to the best-documented anomaly (PEAD) using data already on disk | information under-reaction |
| **4** | **B3 Donchian breakout** | The canonical trend entry; fat-tailed payoff suits a fixed cost; totally objective | supply/demand imbalance |
| **5** | **E1 Low volatility** | Only structural (not behavioural) explanation in the library ⇒ least likely to be arbitraged; lowest turnover | leverage constraints |

**First: B1 (time-series momentum).** It is the only candidate that is
simultaneously (a) the most replicated effect in the literature, (b) implementable
on the current interface with zero platform change, (c) nearly parameter-free —
so a FAIL is informative about the *market* rather than about my encoding — and
(d) squarely aimed at the one gap the evidence actually identifies: at the longest
horizon ever measured here, the POINT edge came within 4% of the 2×-cost bar.

**Post-D-028 correction to that framing:** the CI leg of the gate is much
further away than D-026 recorded — ema200_daily's honest CI-low at the 8-day
horizon is 6.2 bps, not 23.4, against a 30.9 bps cost. The point-edge trend with
horizon still motivates B1; the corrected CI means the likeliest honest outcomes
for ANY long-horizon candidate on 3.5 years of data are FAIL or a weak
borderline. **That expectation is pre-registered here, before B1 is built.** The
research value of B1 is the measured gross-edge magnitude at multi-week
horizons — the first such number this platform will ever produce — not the
verdict itself.

**Its most likely failure is known in advance and must be pre-registered:** 3.5
years contains ~3 independent 12-month periods. If B1 passes, that is *weak*
evidence, and it must be labelled weak at the time the horizon is declared — not
argued about afterwards.

_The formal tier assignments, the diversified ten-strategy queue, and the
per-strategy effort/risk/dependency table now live in
`research/IMPLEMENTATION_ROADMAP.md` (Phase 8.5)._

---

## 7. Method notes

- **No strategy here is claimed profitable.** Every reference explains why an
  effect is *believed* to exist; none is evidence it survives NSE costs in 2026.
- **Counter-references are cited deliberately** (Sullivan/Timmermann/White vs
  Brock/Lakonishok/LeBaron; Avramov/Chordia/Goyal vs reversal; Aronson vs
  technical rules; Daniel/Moskowitz vs momentum). A library that cites only
  supporting work is marketing.
- **Most references are US/developed-market.** India-specific evidence exists for
  momentum (IIMA factor library) and low volatility; for the rest, transfer is an
  assumption to be **measured, not asserted**.
- **Priority is not a profitability forecast.** It ranks *evidence quality ×
  cost survivability × implementability × overfitting surface*.
