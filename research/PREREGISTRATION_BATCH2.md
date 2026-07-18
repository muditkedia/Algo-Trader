# Batch-2 Pre-registration

_Phase 11, 2026-07-18. Written and committed BEFORE implementation and BEFORE any
measurement, on the expanded corpus (NIFTY-500, up to ~11 years daily). Every
parameter is frozen; nothing may change after a verdict exists. Measured under
the FROZEN Phase-10 methodology (D-031) — unchanged. A FAIL is a result._

## Why this batch is fundamentally different from batch 1

Batch 1 was eight **per-symbol** rules (each stock judged on its own history:
own-trend momentum, a 52-week-high threshold, a gap, a channel break, a spring,
a squeeze, a volume spike, a pullback). All failed — none showed a selection
edge over a random entry. **Batch 2 is predominantly CROSS-SECTIONAL**: the
signal for a stock on a date depends on its RANK among all ~500 peers that day.
This is a genuinely different question — "is this stock better than its peers
right now" rather than "is this stock's own chart bullish" — and it is the form
in which the equity-factor literature actually documents these effects. It
requires the cross-sectional seam (an additive `prepare_cross_section` hook; no
change to the measurement or the gate).

The expanded corpus is the other half: ~11 years × 500 names gives a
monthly-rebalanced cross-sectional strategy ~130 monthly cross-sections and ~10
independent annual periods, versus batch 1's ~3 — directly attacking the power
problem D-031/L-011 exposed.

## Part B — removals from the roadmap (rejected-idea variants, NOT implemented)

| Removed | Why (which rejected batch-1 hypothesis it repeats) |
|---|---|
| B2 200-day MA trend | Own-trend trend-following = ema200_daily / tsmom_daily (both FAILED). A moving-average re-parameterization of a dead idea. |
| C1 Minervini VCP | Volatility compression = squeeze_daily (FAILED). More parameters, same hypothesis. |
| C3 NR7 multi-week | Compression again = nr7_daily / squeeze (FAILED). |
| D3 high-volume premium | IS hvol_daily (FAILED) — already tested. |
| Darvas / Turtle breakout variants | Channel breakout = donchian55_daily (FAILED). |
| B7 Elder / pullback variants | Pullback continuation = triple_screen_daily (FAILED). |

These are removed because re-testing a rejected per-symbol hypothesis with
tweaked parameters is exactly the curve-fitting the project forbids. Note the
distinction that IS kept: the CROSS-SECTIONAL RANK forms of momentum and 52-week
high are **not** variants of the rejected per-symbol/threshold forms — they test
a different mechanism (relative rank vs absolute level) and are included.

## Shared rules (all batch-2 strategies)

- **Timeframe** `1d`, **long-only**, **product = DELIVERY** (SWING).
- **Exits: platform-uniform** (D-006) — ATR/structure stop, monotonic trail,
  horizon end. NO per-strategy exits.
- **Cross-sectional strategies hold the FAVOURED side long-only**: the top decile
  for momentum/quality, the bottom decile for volatility/beta/reversal. Rebalance
  is expressed as an edge-triggered ENTRY when a stock enters the favoured set
  (exits handled uniformly), so the existing pipeline is unchanged.
- **Universe: configurable** (NIFTY-100 or NIFTY-500). Cross-sectional ranks are
  computed over whatever universe is measured.
- Judged by the unchanged D-031 gate: selection edge (vs random entry) CI-low >
  cost, AND the absolute §7 bars.
- **Independent-sample note:** cross-sectional signals cluster on rebalance dates
  and share one market history, so the effective independent count is closer to
  the number of rebalance periods × regime diversity than to the raw signal
  count. Stated per strategy; deliberately conservative.

---

## The 13 candidates (Part C) with pre-registration (Part D)

### Momentum family (relative, not own-trend — batch-1's tsmom was own-trend sign)

#### 1. xsmom_daily — cross-sectional momentum (12-1)
- **Hypothesis:** stocks in the top decile of 12-month return (skip last month)
  outperform the bottom decile over the next 1–3 months (Jegadeesh-Titman 1993;
  Fama-French-Carhart; IIMA India factors).
- **Why different from rejected:** tsmom_daily tested the SIGN of a stock's own
  return (time-series). This ranks stocks against each other — the documented
  cross-sectional effect, a different mechanism.
- **Entry:** stock enters the top `decile` (10%) of the universe's 12-1 momentum.
- **Exit:** uniform. **Horizon:** 21–63 bars. **Regime:** trending/bull.
- **Data:** 252/21-lagged closes, cross-section. **`horizon_bars`=(21,42,63)**,
  **`max_hold_bars`=63**.
- **Expected independent samples:** ~130 monthly cross-sections / ~10 yrs.
- **Frozen:** formation 252, skip 21, decile 0.10.

#### 2. resmom_daily — residual (market-adjusted) momentum
- **Hypothesis:** momentum on returns residual to the market isolates
  stock-specific under-reaction and avoids momentum-crash factor bets (Blitz-
  Huij-Martens 2011); historically better risk-adjusted than raw momentum.
- **Why different:** neither batch-1 nor xsmom removes market beta from the
  ranking signal; this does.
- **Entry:** top decile of trailing 126-day residual cumulative return, where
  residual = stock return − beta·market return (beta from the same window;
  market = equal-weight universe).
- **Exit:** uniform. **Horizon:** 21–63. **Regime:** most.
- **Data:** rolling market regression, cross-section. **`horizon_bars`=(21,42,63)**,
  **`max_hold_bars`=63**. **Independent:** ~130 monthly.
- **Frozen:** window 126, decile 0.10, market = EW universe.

#### 3. dualmom_daily — dual momentum (relative + absolute)
- **Hypothesis:** combine cross-sectional strength (top decile) with an absolute
  trend filter (own 12-month return > 0), so the book steps aside in bear markets
  where relative momentum crashes (Antonacci 2014).
- **Why different:** adds a regime-conditional absolute gate to the relative
  rank — distinct from both tsmom (pure absolute) and xsmom (pure relative).
- **Entry:** in the top momentum decile AND own 12-1 return > 0.
- **Exit:** uniform. **Horizon:** 21–63. **Regime:** bull; flat in bear.
- **`horizon_bars`=(21,42,63)**, **`max_hold_bars`=63**. **Independent:** ~130.
- **Frozen:** formation 252, skip 21, decile 0.10, absolute floor 0.

### Low-risk family (structural anomaly — nothing in batch 1)

#### 4. lowvol_daily — low volatility
- **Hypothesis:** low-total-volatility stocks earn higher risk-adjusted returns
  than high-vol (Ang-Hodrick-Xing-Zhang 2006; Blitz-van Vliet 2007; India:
  Joshipura). A structural (leverage-constraint) explanation, not behavioural,
  so less arbitraged.
- **Why different:** a risk-ranking hypothesis; batch 1 had no risk-based
  selection.
- **Entry:** stock enters the bottom decile of trailing 126-day return volatility.
- **Exit:** uniform. **Horizon:** 42–126 (low turnover). **Regime:** all; lags in
  sharp bulls.
- **`horizon_bars`=(21,63,126)**, **`max_hold_bars`=126**. **Independent:** ~50
  (bi-monthly-ish, lowest turnover in the batch).
- **Frozen:** vol window 126, decile 0.10.

#### 5. bab_daily — betting against beta
- **Hypothesis:** low-beta stocks deliver higher risk-adjusted returns than
  high-beta; leverage-constrained investors bid up high beta (Frazzini-Pedersen
  2014). Long-only expression: hold the low-beta decile.
- **Why different from lowvol:** ranks on market BETA (systematic) not total
  volatility — a different, standard factor.
- **Entry:** bottom decile of trailing 252-day beta to the EW-universe market.
- **Exit:** uniform. **Horizon:** 42–126. **Regime:** all; lags in bulls.
- **`horizon_bars`=(21,63,126)**, **`max_hold_bars`=126**. **Independent:** ~50.
- **Frozen:** beta window 252, decile 0.10.

### Reversal / behavioural (distinct from everything in batch 1)

#### 6. xsrev_daily — cross-sectional short-term reversal
- **Hypothesis:** last week's cross-sectional losers rebound over the next 1–4
  weeks as liquidity-driven price pressure unwinds (Jegadeesh 1990; Lehmann
  1990). Long-only: hold the prior-week loser decile.
- **Why different:** batch 1 had NO mean-reversion; opposite sign to momentum.
- **Known weakness (pre-registered):** reversal profits concentrate in illiquid
  names and are largely eaten by cost (Avramov-Chordia-Goyal) — the gate should
  catch this, and it is the point of testing it honestly.
- **Entry:** bottom decile of trailing 5-day return.
- **Exit:** uniform. **Horizon:** 5–21. **Regime:** range/all.
- **`horizon_bars`=(5,10,21)**, **`max_hold_bars`=21**. **Independent:** ~520
  weekly cross-sections (highest turnover; cost-hostile).
- **Frozen:** lookback 5, decile 0.10.

#### 7. maxret_daily — anti-lottery (low MAX effect)
- **Hypothesis:** stocks with extreme recent single-day returns are over-bought
  by lottery-seeking investors and subsequently underperform; holding the LOW-MAX
  decile avoids that drag and earns a premium (Bali-Cakici-Whitelaw 2011).
- **Why different:** a behavioural cross-sectional anomaly on the DISTRIBUTION of
  recent returns (extreme-day), unrelated to level, trend or volatility ranking.
- **Entry:** bottom decile of the maximum daily return over the trailing 21 days.
- **Exit:** uniform. **Horizon:** 21–63. **Regime:** all.
- **`horizon_bars`=(21,42,63)**, **`max_hold_bars`=63**. **Independent:** ~130.
- **Frozen:** MAX window 21, decile 0.10.

### Relative strength / anchoring (RANK form — not batch-1's threshold)

#### 8. hi52rank_daily — 52-week-high proximity, cross-sectional rank
- **Hypothesis:** George-Hwang (2004) found the 52-week-high effect is
  cross-sectional: stocks CLOSEST to their own 52-week high (top decile of
  close/52w-high) outperform, dominating conventional momentum.
- **Why different from the rejected hi52_daily:** hi52_daily was a per-symbol
  ABSOLUTE threshold (crossed into the top 5% band). This RANKS the ratio across
  the universe — the form the paper actually documents. Different mechanism.
- **Entry:** top decile of close ÷ trailing-252-day high.
- **Exit:** uniform. **Horizon:** 21–126. **Regime:** bull.
- **`horizon_bars`=(21,63,126)**, **`max_hold_bars`=126**. **Independent:** ~80.
- **Frozen:** lookback 252, decile 0.10.

### Calendar / regime / structural / liquidity / composite

#### 9. tom_daily — turn-of-month
- **Hypothesis:** returns concentrate around month boundaries (salary/SIP inflows
  — India has large, dated monthly systematic-investment flows) (Ariel 1987;
  Lakonishok-Smidt 1988). A specific, dated demand mechanism, not a mined quirk.
- **Why different:** a calendar/seasonality hypothesis — no batch-1 analogue.
- **Entry:** the last trading bar on/before the 1-trading-day window that begins
  `tom_before` (2) sessions before month-end; edge-triggered once per month.
- **Exit:** uniform (short hold). **Horizon:** 3–7. **Regime:** all.
- **`horizon_bars`=(3,5,7)**, **`max_hold_bars`=7**. **Data:** per-symbol calendar
  (no cross-section). **Independent:** ~130 months (one event/month).
- **Frozen:** window −2..+3 sessions around month-end.

#### 10. breadth_regime_daily — momentum gated by market breadth
- **Hypothesis:** trend/momentum works only when market participation is broad;
  gating entries on breadth (% of universe above its 200-day MA > 50%) avoids
  whipsaws in narrow/toppy markets (regime-dependent behaviour).
- **Why different:** a REGIME-CONDITIONAL strategy using a cross-sectional
  AGGREGATE (breadth) as a filter — a distinct construct (market-breadth family).
- **Entry:** stock closes above its own 50-day high AND universe breadth (% above
  200-day MA) > 0.5 on that date.
- **Exit:** uniform. **Horizon:** 21–63. **Regime:** broad-bull only.
- **`horizon_bars`=(21,42,63)**, **`max_hold_bars`=63**. **Independent:** ~120
  (breadth is highly autocorrelated — conservatively fewer).
- **Frozen:** breadth MA 200, threshold 0.5, breakout 50.

#### 11. stage2_daily — Weinstein stage analysis (per-symbol, structural)
- **Hypothesis:** a Stage 1→2 transition — breakout from a base above a rising
  30-week MA on volume expansion — catches the markup phase (Weinstein 1988).
- **Why different:** an explicit multi-condition STRUCTURAL regime thesis
  (base + rising long MA + volume), not a single breakout (donchian) or pullback.
  Prompt explicitly requests stage analysis.
- **Entry:** close crosses a 30-week (150-day) high AND 30-week MA rising AND
  volume > 1.5× its 50-day average.
- **Exit:** uniform. **Horizon:** 42–126. **Regime:** bull.
- **`horizon_bars`=(21,63,126)**, **`max_hold_bars`=126**. **Data:** per-symbol.
  **Independent:** ~60.
- **Frozen:** MA 150, high 150, volume 1.5×/50.

#### 12. illiq_daily — Amihud illiquidity premium
- **Hypothesis:** less-liquid stocks earn a premium for illiquidity (Amihud
  2002). NIFTY-500 (vs the NIFTY-100 where the roadmap called this dead) spans a
  real liquidity range in its 300–500 tail, so it is now testable.
- **Why different:** a LIQUIDITY hypothesis — no batch-1 analogue; explicitly
  requested.
- **Known weakness (pre-registered):** the premium is compensation for the very
  spread/impact the flat 2 bps slippage model understates; if the gate passes it,
  the cost model must be revisited before any deployment.
- **Entry:** top decile of Amihud illiquidity = mean(|return| ÷ traded-value) over
  63 days.
- **Exit:** uniform. **Horizon:** 42–126. **Regime:** all.
- **`horizon_bars`=(21,63,126)**, **`max_hold_bars`=126**. **Independent:** ~60.
- **Frozen:** window 63, decile 0.10.

#### 13. combo_lowvol_mom_daily — factor composite (low-vol × momentum)
- **Hypothesis:** combining two imperfectly-correlated factor signals (low
  volatility and momentum) yields a more robust cross-sectional score than either
  alone (Asness et al.; factor-investing practice).
- **Why different:** tests factor COMBINATION — whether a composite rank survives
  where singles may not — a distinct question from any single-factor sort.
- **Entry:** top decile of the average of (momentum percentile) and (inverse-
  volatility percentile).
- **Exit:** uniform. **Horizon:** 42–126. **Regime:** all.
- **`horizon_bars`=(21,63,126)**, **`max_hold_bars`=126**. **Independent:** ~80.
- **Frozen:** momentum 252/21, vol 126, decile 0.10, equal weights.

---

## Coverage check (Part C diversity)

| Requested family | Candidate(s) |
|---|---|
| Cross-sectional momentum | xsmom (1) |
| Residual momentum | resmom (2) |
| Relative strength | hi52rank (8) |
| Dual momentum | dualmom (3) |
| Low volatility | lowvol (4) |
| Mean reversion | xsrev (6) |
| Calendar effects | tom (9) |
| Behavioural anomalies | maxret (7) |
| Liquidity effects | illiq (12) |
| Factor investing | bab (5), combo (13) |
| Stage analysis | stage2 (11) |
| Regime-dependent | breadth_regime (10) |
| Cross-sectional ranking | 1,2,3,4,5,6,7,8,12,13 |
| Market breadth | breadth_regime (10) |

13 strategies, every requested family represented, 10 of them genuinely
cross-sectional (impossible to express in batch 1's per-symbol interface).

## Pre-registered batch expectation

More history and breadth raise power, but three headwinds are pre-registered so a
FAIL is read correctly: (a) long-only truncates the short leg of every
cross-sectional factor, roughly halving its documented spread; (b) the flat cost
model is optimistic exactly where reversal (6) and illiquidity (12) live; (c) one
shared market history caps independence regardless of symbol count. The realistic
good outcome is 0–2 survivors with genuine, cost-clearing selection edge; the
research value is the first cross-sectional edge measurements this platform has
ever produced, at adequate power.
