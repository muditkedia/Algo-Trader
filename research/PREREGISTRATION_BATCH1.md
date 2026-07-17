# Batch-1 Pre-registration

_Phase 9, 2026-07-17. Written and committed BEFORE implementation and BEFORE any
measurement. Every parameter below is frozen: nothing may be changed after a
verdict exists. A FAIL is a result._

## Scope and shared rules

Eight strategies from the roadmap's per-symbol queue (slots #1–#8) — the
cross-sectional pair (A1, E1) waits on the seam, exactly as the roadmap
sequences it. All eight:

- **Timeframe** `1d`, **long-only**, **product = DELIVERY** (holding scope SWING
  → the engine applies the full 30.9 bps round-trip delivery cost stack).
- **Exits: platform-uniform**, per D-006 — initial ATR/structure stop, monotonic
  trailing ratchet, horizon end at `max_hold_bars`. NO strategy-specific exits.
- **Entries are edge-triggered** (fire on the transition bar only).
- Judged by the unchanged pre-registered bars (D-007 2×-cost on the D-028
  block-bootstrap CI lower bound, §7 PF/expectancy floors, §15 2×-cost
  survival, 30-signal minimum).
- Confidence scores are minimal heuristics recorded for calibration only
  (L-003: they are hypotheses, not probabilities).

**Independent-sample arithmetic used below** (~870 usable trading days):
5d→~174 blocks · 10d→~87 · 20d→~43 · 30d→~29 · 40d→~21 · 60d→~14.
**Pre-registered expectation for the whole batch:** with block CIs this wide and
a 0-for-6 platform base rate, the likeliest outcome is most or all of the batch
FAILING the CI leg. The deliverable is the measured gross-edge profile per
hypothesis family, which reprices the roadmap's priors.

Signal-count figures below are ex-ante order-of-magnitude estimates, not
commitments.

---

## 1. tsmom_daily — time-series momentum (roadmap B1)

- **Hypothesis:** a stock's own 12-month return (skipping the most recent month)
  predicts its next 1–3 months (under-reaction / slow information diffusion;
  Moskowitz-Ooi-Pedersen 2012).
- **Entry:** `close.shift(21)/close.shift(252) − 1` **crosses above 0**.
- **Exit:** platform-uniform. **Horizon:** 1–3 months.
- **Regime:** trending/bull. **Indicators:** 252d/21d lagged closes only.
- **`horizon_bars` = (5, 10, 20, 40, 60)** · **`max_hold_bars` = 60**.
- **Expected independent samples:** ~14 blocks at the 60d horizon — the weakest
  measurement in the batch, pre-registered as such. Signals est. 200–600.
- **Params frozen:** formation 252, skip 21, threshold 0.0.

## 2. hi52_daily — 52-week-high proximity (roadmap A3)

- **Hypothesis:** traders anchor on the 52-week high and under-react to news
  near it; clearing the anchor releases the move (George & Hwang 2004).
- **Entry:** `close / max(high, 252d prior)` **crosses above 0.95**.
- **Exit:** platform-uniform. **Horizon:** 2 wk – 3 mo.
- **Regime:** bull. **Indicators:** rolling 252d high (shifted 1).
- **`horizon_bars` = (10, 20, 40, 60)** · **`max_hold_bars` = 60**.
- **Expected independent samples:** ~14–87 by horizon. Signals est. 500–1500.
- **Params frozen:** lookback 252, band 0.95.

## 3. egap_daily — earnings-gap continuation, price-only PEAD proxy (roadmap D2)

- **Hypothesis:** a large up-gap on extreme volume marks an information event;
  under-reaction (PEAD, Bernard & Thomas 1989) drifts the price further over
  the next 1–2 months. Proxy: no earnings feed — gap+volume stand in.
- **Entry (all on the gap day, at its close):** `open/prev_close − 1 ≥ 3%` AND
  `volume ≥ 3× its 20d mean` AND `close ≥ open` (gap held, not faded).
- **Exit:** platform-uniform. **Horizon:** 1–8 weeks.
- **Regime:** any. **Indicators:** gap %, volume_ratio(20).
- **`horizon_bars` = (5, 10, 20, 40)** · **`max_hold_bars` = 40**.
- **Expected independent samples:** ~21–174 by horizon. Signals est. 500–1500.
- **Params frozen:** gap 3%, volume 3×/20d, hold-through-close required.

## 4. donchian55_daily — 55-day channel breakout (roadmap B3, plain variant)

- **Hypothesis:** a new 55-day high marks a supply/demand imbalance; large
  trends begin at new highs (Donchian; Turtle System 2).
- **Entry:** close **crosses above** the prior 55-day high.
- **Exit:** platform-uniform. **Horizon:** 2 wk – 3 mo.
- **Regime:** trending. **Indicators:** rolling 55d high (shifted 1).
- **`horizon_bars` = (10, 20, 40, 60)** · **`max_hold_bars` = 60**.
- **Expected independent samples:** ~14–87. Signals est. 1000–3000.
- **Params frozen:** channel 55.

## 5. wyckoff_spring_daily — failed-breakdown reclaim (roadmap H1)

- **Hypothesis:** a break below a multi-week range low that snaps back inside
  within ≤3 bars trapped breakdown sellers at a level where larger buyers
  absorbed supply; their covering fuels the markup (Wyckoff spring).
- **Entry:** with `range_low` = prior 30-day min low (shifted 1): either the
  breakdown bar itself closes back above `range_low` (same-bar spring), or
  within the next 2 bars close **crosses back above** `range_low`.
- **Exit:** platform-uniform. **Horizon:** 1–8 weeks.
- **Regime:** range→trend transition. **Indicators:** rolling 30d low.
- **`horizon_bars` = (5, 10, 20, 40)** · **`max_hold_bars` = 40**.
- **Expected independent samples:** ~21–174. Signals est. 300–1000.
- **Params frozen:** range 30d, reclaim window 3 bars total.

## 6. squeeze_daily — weekly volatility squeeze breakout (roadmap C2)

- **Hypothesis:** volatility clusters; when the weekly Bollinger bands contract
  inside the weekly Keltner channel, energy is stored, and an upside range
  break resolves it directionally (Bollinger; Carter).
- **Entry:** most recent **completed** week has BB(20,2.0) fully inside
  KC(20, 1.5×ATR) on weekly bars, AND the daily close **crosses above** the
  prior 10-day high.
- **Exit:** platform-uniform. **Horizon:** 1–6 weeks.
- **Regime:** compression→expansion. **Indicators:** weekly BB/KC (completed
  weeks only, as-of joined to daily), 10d high.
- **`horizon_bars` = (5, 10, 20, 30)** · **`max_hold_bars` = 30**.
- **Expected independent samples:** ~29–174. Signals est. 200–800.
- **Params frozen:** weekly 20/2.0 BB, 20/1.5 KC, 10d trigger.
- **Pre-registered expected failure mode:** compression predicts a move, not
  its sign (volexp_1h's exact killer, now at survivable scale).

## 7. hvol_daily — high-volume return premium (roadmap D3)

- **Hypothesis:** abnormal volume = attention/visibility shock that expands a
  stock's investor base and lifts it over 1–4 weeks (Gervais, Kaniel &
  Mingelgrin 2001). Direction-unconditional in the source, long-only here.
- **Entry:** `volume / 50d mean` **crosses above 3.0**.
- **Exit:** platform-uniform. **Horizon:** 1–4 weeks.
- **Regime:** any. **Indicators:** volume_ratio(50).
- **`horizon_bars` = (5, 10, 20)** · **`max_hold_bars` = 20**.
- **Expected independent samples:** ~43–174. Signals est. 1000–2500.
- **Params frozen:** ratio 3.0, window 50.

## 8. triple_screen_daily — Elder triple screen (roadmap B7)

- **Hypothesis:** trade with the weekly tide, enter on the daily wave against
  it: weekly uptrend + daily pullback + strength trigger (Elder 1993).
- **Entry:** weekly EMA(13) rising as of the most recent completed week, AND
  yesterday's 2-day-EMA Force Index < 0 (pullback), AND close **crosses above**
  yesterday's high (trigger).
- **Exit:** platform-uniform. **Horizon:** 3 days – 4 weeks.
- **Regime:** trending. **Indicators:** weekly EMA13 (completed weeks, as-of
  joined), Force Index EMA-2.
- **`horizon_bars` = (3, 5, 10, 20)** · **`max_hold_bars` = 20**.
- **Expected independent samples:** ~43–290. Signals est. 2000–6000.
- **Params frozen:** weekly EMA 13, FI smoothing 2, trigger = prior day's high.
- **Lookahead rule, pre-registered:** weekly values join to a daily bar only if
  the week COMPLETED on or before that bar's date (a Friday close completes its
  own week). A truncation test must prove earlier signals are unchanged.

---

## Selection rationale (Part A)

One hypothesis family each, per the roadmap's diversity rule: own-trend
persistence (1), anchoring/relative strength (2), event under-reaction/gap (3),
breakout (4), trapped-liquidity price action (5), volatility compression (6),
volume/attention (7), multi-timeframe pullback (8). Trend-following is
represented by both its momentum form (1) and its breakout form (4) — the two
canonical, historically distinct implementations. Mean reversion at short
horizons is deliberately absent (cost-hostile per Avramov-Chordia-Goyal and
D-026); its surviving form here is the spring (5).
