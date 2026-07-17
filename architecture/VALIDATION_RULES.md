# Algo Trader — Validation Rules (FROZEN)

_Status: **FROZEN & APPROVED 2026-07-14.** This is the official validation
methodology for the project. Changes require explicit approval and must be
recorded in docs/DECISIONS.md. Metric thresholds are pre-registered: amending
them after out-of-sample data has been seen invalidates the run._

---

## Guiding principles

1. **Structural validity before performance.** A backtest number is
   meaningless if the strategy peeks at the future. Lookahead/recursive
   analysis is Gate 0 (§17) — nothing downstream is trusted until it passes.
2. **Smoke-test before scale.** Prove the entire pipeline on a tiny sample
   (§18) before the multi-year, multi-pair download.
3. **Pre-registration & OOS integrity.** Every threshold in §7 and any future
   parameter search space are fixed before out-of-sample data is looked at.
   The locked holdout is looked at exactly once, at the very end.
4. **Risk-adjusted, not raw-return, acceptance.** No fixed CAGR floor; the
   bar is risk-adjusted quality, regime stability, and cross-pair
   consistency (§7).
5. **Costs and robustness are first-order.** Every verdict is on net
   (post-cost) results and must survive stress (§15) and parameter
   perturbation (§13).
6. **Decide on distributions and lower bounds, not point estimates.**
   Monte-Carlo confidence intervals gate acceptance (§14).
7. **Benchmark-relative, not absolute.** A verdict is on the edge OVER a
   baseline that shares the strategy's market exposure — never on absolute
   return, which market drift can supply on its own (§26, D-031).

---

## 1. Exchange

**Binance (spot).** Matches the production config (`exchange.name: binance`,
`trading_mode: spot`) and the intended live venue — validating on a different
exchange's data would introduce a data-distribution mismatch. All OHLCV is
pulled via Freqtrade `download-data`.

## 2. Trading pairs

| Tier | Pairs | Role |
|---|---|---|
| **In-sample (design)** | `BTC/USDT`, `ETH/USDT` | Production whitelist; all design and any tuning happen here only. |
| **Held-out (unseen)** | `SOL, BNB, XRP, ADA, DOGE, LINK, AVAX, LTC` /USDT | Never tuned on; backtested once at the end with the locked config to test generalization. |

Why each unseen pair contributes (chosen to span distinct volatility, beta,
market-cap, and idiosyncratic-driver profiles):

| Pair | Distinct stress it adds |
|---|---|
| **SOL** | High-beta L1: violent momentum trends and sharp reversals — tests trend capture + stop behaviour in fast markets. |
| **BNB** | Exchange token with idiosyncratic drivers (burns, Binance flow); lower market beta — tests edge that isn't pure BTC-beta. |
| **XRP** | Long range-bound stretches punctuated by news/legal jumps — tests range regimes and gap/jump handling. |
| **ADA** | Large-cap laggard with weak, choppy trends — stresses the ADX/trend gates where trend quality is marginal. |
| **DOGE** | Sentiment/meme-driven, fat-tailed, event spikes — tests fat-tail volatility and the volume-confirmation logic. |
| **LINK** | Mid-cap with multi-week trends and deep pullbacks — tests trend persistence and pullback tolerance. |
| **AVAX** | Second high-beta L1 on a different cycle timing than SOL — reduces single-name dependence in the high-beta bucket. |
| **LTC** | Old, lower-volatility major with muted trends — tests low-vol / low-ADX conditions where the volatility floor and liquidity gates bind. |

Coverage: high-beta L1s (SOL, AVAX), exchange token (BNB), range/news (XRP),
laggard (ADA), fat-tail meme (DOGE), oracle mid-cap (LINK), low-vol major
(LTC). Caveat: all correlate with BTC, so this diversifies volatility and
idiosyncrasy, not market-wide beta.

## 3. Candle timeframes

| TF | Why |
|---|---|
| **1m** | Intra-candle fill/stop simulation via `--timeframe-detail 1m` — essential for a stop-driven strategy. |
| **5m** | Base trading timeframe. |
| **15m / 1h / 4h** | Informative timeframes consumed by the decision engine and profile. |
| **1d** | Regime labelling (§11) and context. |

**Warmup pre-roll:** `startup_candle_count = 3600` (≈12.5 days of 5m; ≈75 4h
candles). Every backtest window must have ≥ 1 month of prior data on disk so
the 4h EMA50 is fully warm at the window's first candle; the download starts
one month before the earliest analysis date.

## 4. Depth of history

**Full corpus: 2020-01-01 → 2026-06-30 (~6.5 years)**, downloaded from
**2019-12-01** for pre-roll. Spans multiple distinct regimes: COVID crash +
recovery (2020), bull + blow-off top (2021), bear (2022), chop/recovery
(2023), bull (2024), 2025 → present. Pre-2020 Binance microstructure is less
representative and is excluded. Minimum acceptable: 3 years; recommended:
the full window.

## 5. Data partitioning

| Segment | Window | Purpose |
|---|---|---|
| Development corpus | 2020-01-01 → 2025-06-30 (5.5y) | All exploration, walk-forward, and any tuning. |
| → Training (IS) | rolling 12-month blocks | Baseline evaluation now; parameter fitting in the later Mode B phase. |
| → Validation (OOS) | rolling 3-month blocks, step 3 months | ~18 folds → concatenated OOS equity curve (2021-01 → 2025-06). |
| Locked holdout (final OOS) | 2025-07-01 → 2026-06-30 (12 mo) | Untouched until the end; single confirmatory run. |
| Cross-pair OOS | holdout window, 8 unseen pairs | Generalization check with the locked config. |

## 6. Success metrics

All from Freqtrade's backtest summary unless noted; evaluated on the
concatenated walk-forward OOS and again on the locked holdout.

CAGR, Sharpe (ann.), Sortino (ann.), Profit Factor, Win Rate,
**Holding-time distribution (§12)**, Max Drawdown (abs/%), Recovery Factor
(net profit ÷ max DD), Expectancy (per trade, net) + expectancy ratio,
Number of Trades. Supporting: Calmar, max-DD duration, % profitable OOS
quarters, per-pair & per-regime profit (§11), portfolio battery (§16),
market exposure. Regime, holding-time, Monte-Carlo, stress, and portfolio
figures come from the companion tooling (§24).

## 7. Acceptance criteria — risk-adjusted (pre-registered)

Applied to the **aggregate OOS** (using Monte-Carlo CI bounds, §14) and
re-confirmed on the **locked holdout**. CAGR / absolute return are reported
for context (and vs buy-and-hold) but only "net positive" is required — they
do not gate.

**Primary risk-adjusted gates (all must pass):**

| Metric | Reject if | Target | Gate uses |
|---|---|---|---|
| Profit Factor | < 1.25 | ≥ 1.50 | point + lower-95% CI ≥ 1.15 |
| Sharpe (ann.) | < 1.0 | ≥ 1.5 | **lower 95% CI ≥ 0.7** |
| Sortino (ann.) | < 1.3 | ≥ 2.0 | point |
| Recovery Factor | < 2.0 | ≥ 3.0 | point |
| Expectancy (net) | ≤ 0 | ≥ 0.15R | **lower 95% CI > 0** |
| Max Drawdown | > 25% | ≤ 15% | **upper 95% CI ≤ 30%** |
| Calmar | < 1.0 | ≥ 2.0 | point |

**Stability gates:**
- ≥ 70% of OOS quarters net-positive; no single quarter below −(½ · MaxDD limit).
- **Regime stability (§11):** profitable & PF > 1 in Bull; in Bear/Range the
  strategy need not be profitable but must be capital-preserving — no regime
  with PF < 0.8 or contributing a drawdown beyond the overall limit.
- **Portfolio gate (§16):** worst-case simultaneous-stop loss ≤ 15% of
  capital, and average capital utilization within ~15–70% (portfolio
  drawdown is already covered by the Max Drawdown gate).

**Consistency gate (unseen pairs, §2):** risk-adjusted edge (PF ≥ 1.2,
expectancy > 0 net) on **≥ 6 of 8** unseen pairs, aggregate unseen-pair
PF ≥ 1.2, with no sign the edge is unique to BTC/ETH.

**Holding-time gate (§12):** interquartile range within the 30–120 min
design band.

**Why risk-adjusted over a fixed CAGR floor:** a CAGR bar rewards taking
more risk/exposure and cannot see drawdown quality; in crypto, absolute
return is dominated by market beta and regime luck, so a fixed CAGR
threshold is arbitrary, invites overfitting *to hit the number*, and
penalizes robust, selective, low-turnover behaviour. Buy-and-hold BTC
out-returns most strategies in bull runs — the strategy's value proposition
is risk-adjusted return and drawdown control, which is exactly what
PF/Sharpe/Sortino/Recovery/Expectancy/MaxDD measure. The project's goal is
long-term, risk-controlled profitability: a distribution-quality question,
not a raw-return one.

## 8. Rejection criteria (any one → fail)

- Gate 0 (§17) or the Pipeline Smoke Test (§18) fails.
- Any §7 gate breached (including CI bounds).
- Holding-time IQR outside 30–120 min, or exits dominated by stop-outs
  rather than objective signal exits.
- Overfit signature: OOS Sharpe < 50% of IS Sharpe; walk-forward efficiency
  < 0.5; or unstable optima across folds (§9, §10).
- Fragility: a ±5–10% threshold perturbation collapses a key metric (§13).
- Cost fragility: net-negative under 2× fees or added slippage (§15).
- Regime fragility: any regime with PF < 0.8 or an outsized regime drawdown (§11).
- Cross-pair failure: edge on fewer than 6 of 8 unseen pairs.
- **Portfolio:** worst-case simultaneous-stop loss > 20% of capital;
  portfolio-drawdown upper-CI > 30%; or max concurrent positions exceeding
  `max_open_trades` (logic error) (§16).
- Insufficient evidence: < 150 aggregate OOS trades, or any regime/pair
  bucket judged on < 30 trades → inconclusive = fail.
- MaxDD upper-CI breach, or Expectancy lower-CI ≤ 0 (§14).

## 9. Overfitting detection

1. **IS vs OOS degradation** — large metric drops (e.g., Sharpe halving) = noise-fitting.
2. **Walk-forward efficiency (WFE)** = OOS return / IS return across folds; ≥ 0.5 required (Mode B).
3. **Parameter stability** — Mode B optima must cluster across folds, not jump.
4. **Sensitivity surfaces (§13)** — defaults must sit on plateaus, not spikes.
5. **Cross-pair OOS (§2)** — the edge must survive outside BTC/ETH without retuning.
6. **Randomized benchmarks** — beat (a) random-entry with the same exit/risk logic and (b) buy-and-hold, on risk-adjusted terms.
7. **Monte-Carlo (§14)** — realized path must sit inside the resampled band; decisions on CI bounds.
8. **Degrees-of-freedom discipline** — track configurations tried; deflated-Sharpe mindset; any search kept to a small, pre-declared subset.

## 10. Walk-forward procedure

**Windows:** rolling **IS = 12 months, OOS = 3 months, step = 3 months**
across the development corpus → ~18 non-overlapping OOS quarters
(2021-01 → 2025-06), concatenated into one OOS equity curve. Rolling (not
anchored) so each fold reflects the regime near its OOS.

- **Mode A — fixed-parameter (run first; fully supported today).** The
  hand-designed `config.algo_trader` defaults are locked. Backtest the full
  development corpus and each OOS quarter with the same config
  (+ `--breakdown month`). Tests whether one honest configuration is robust
  across regimes — the least overfit-prone first pass. Aggregate OOS is
  evaluated against §7.
- **Mode B — reoptimizing (later, optional; code prerequisite).** Per fold:
  fit the pre-declared parameter subset on the 12-month IS, lock, evaluate
  untouched on the 3-month OOS, step, repeat; stitch OOS and compute WFE.
  Prerequisite: the strategy currently exposes zero Freqtrade hyperopt
  parameters, so Mode B needs either surfacing chosen `algo_trader`
  thresholds as hyperopt parameters (code change) or a scripted config-sweep
  harness — a separate approved task.

### 10.1 Retraining-cadence evaluation (Mode B only)

The walk-forward step *is* the retraining cadence. Before fixing one, Mode B
evaluates three cadences on the same development corpus, holding IS lookback
constant at 12 months to isolate the cadence effect:

| Cadence | Window design | Folds over dev (5.5y) |
|---|---|---|
| **Monthly** | IS 12mo, OOS 1mo, step 1mo | ~54 |
| **Quarterly** (prior) | IS 12mo, OOS 3mo, step 3mo | ~18 |
| **Semi-annual** | IS 12mo, OOS 6mo, step 6mo | ~9 |

| | Monthly | Quarterly | Semi-annual |
|---|---|---|---|
| **Advantages** | Fastest adaptation to regime shifts; params track recent character; shortest lag after a structural break. | Balances adaptivity vs. stability; ≥ 3-mo OOS per fold is statistically meaningful; calmer redeployment. | Most stable params; fewest interventions; longest, most robust OOS blocks; lowest ops burden. |
| **Disadvantages** | Param churn/whipsaw; many interventions; heaviest ops; high param variance. | ~3-mo lag to adapt; may hold stale params through a fast transition. | Slowest adaptation; can carry stale params across a major regime break for up to 6 mo; fewest folds. |
| **Compute cost** | Highest (~54 optimizations; ~3× quarterly). | Moderate (~18). | Lowest (~9). |
| **Overfitting risk** | Highest — frequent refits on short increments fit recent noise; most multiple-testing. | Moderate. | Lowest. |
| **Production use** | Short-lived edges / regime-unstable markets, only with infra for safe frequent redeploys and small param deltas. | Default for medium-frequency edges with quarter-scale regime persistence. | Structural, slow-moving edges; conservative deployments; limited compute/ops. |

**Objective selection criteria (pre-registered; not arbitrary):**

1. **Risk-adjusted OOS** — §7 battery on the lower-95% MC bound (§14), not point estimates.
2. **Overfitting-adjusted** — WFE ≥ 0.5 required; an OOS "win" with poor IS retention is noise.
3. **Parameter stability** — turnover/variance across folds below a pre-set ceiling; jumpy optima disqualify.
4. **Robustness of the advantage** — a faster cadence's edge must be statistically significant (MC CIs) and consistent across regimes (§11) and unseen pairs (§2).
5. **Parsimony / cost-benefit** — adopt the **slowest cadence statistically indistinguishable from, or better than, faster ones**. A faster cadence is chosen only if it beats the slower by ≥ 10% relative Sharpe at the lower CI, keeps WFE ≥ 0.5, keeps params stable, and holds across regimes + pairs.

**Prior/default: Quarterly**, overridden only by this objective comparison.

## 11. Market-regime reporting

Every backtest report breaks results out by **Bull, Bear, Range,
High Volatility, Low Volatility**, using two orthogonal, objective,
lookahead-safe classifiers computed per traded pair on its own daily candles
(independent of the strategy's signals and of the RegimeDetector module, to
avoid circularity).

**Trend axis (daily):** `EMA50_d`, slope = `EMA50_d[t] − EMA50_d[t−10]`,
`ADX_d(14)`.
- **Bull:** `close_d > EMA50_d` AND slope > 0 AND `ADX_d ≥ 20`.
- **Bear:** `close_d < EMA50_d` AND slope < 0 AND `ADX_d ≥ 20`.
- **Range:** `ADX_d < 20`, or direction mixed (neither Bull nor Bear fully met).

**Volatility axis (daily, lookahead-safe):** `vol_d = ATR%(14, daily)`;
reference = trailing 365-day rolling percentile of `vol_d` (prior data only,
reproducible live).
- **High Volatility:** `vol_d >` 70th percentile of the trailing window.
- **Low Volatility:** `vol_d <` 30th percentile. (Middle 40% = "Normal",
  reported for completeness.)

**Assignment & calculation:**
- Each closed trade is labelled by its **entry-day** trend and vol regime
  (primary). Exit-day labels are also computed; "cross-regime" trades
  (labels differ) are flagged and their share reported.
- Metrics per regime: subset the exported trade list by label; recompute the
  full metric set per subset. Sharpe/Sortino/MaxDD use the subset's
  time-ordered return series. A bucket needs **≥ 30 trades** before its
  ratios are trusted (§8).
- Report the five named marginals **and** the 3×3 trend×vol matrix (net P&L,
  PF, win rate, expectancy, holding time, #trades per cell). Optional
  overlay: BTC-macro regime labels for market-wide dependence.
- Regime labels use only trailing data — identical to a live classifier.

## 12. Holding-time distribution

Reported **overall, per regime, and per exit reason**: **Average, Median,
P25, P75, P90, Max** (and Min), plus a histogram. Purpose: confirm the
strategy *naturally* produces ~1-hour trades rather than a mean dragged by
outliers.

Acceptance is on the distribution, not the mean:
- **Median 30–120 min** (ideal 45–90); **IQR (P25–P75) inside 20–150 min**.
- P90 tail may extend (no time exits ⇒ long trend-riders are fine if
  profitable); **Max** is flagged, not auto-rejected.
- **Bimodality flag:** a cluster of near-instant stop-outs plus a cluster of
  long holds masquerading as a "1-hour average" is a design red flag and is
  called out explicitly.

## 13. Parameter robustness / sensitivity (±5% / ±10% / ±20%)

**Every configurable threshold** in `settings.py` is perturbed independently
(one-at-a-time) across multipliers **{0.80, 0.90, 0.95, 1.00, 1.05, 1.10, 1.20}**:

- **EngineParams:** `adx_min_1h, adx_min_15m, rsi_min, rsi_max, rsi_ideal,
  volume_ratio_min, atr_pct_min, atr_pct_max, min_quote_volume,
  max_spread_pct, min_score, weight_rsi, weight_volume, weight_volatility`.
- **RiskParams:** `atr_stop_multiplier, trail_atr_multiplier,
  trail_activation_profit, reward_atr_multiple, min_risk_reward,
  risk_per_trade, exit_adx_floor, exit_rsi_floor`, and each
  `profit_lock_tier`. `hard_stop_pct` is tested but production-capped at 6%
  regardless of what the sweep prefers (safety overrides optimization).
- **IndicatorParams (periods):** perturb with integer rounding; where ±5%
  rounds to no change, substitute a ±1-period step.

**Generation:** run at a representative multi-regime window (e.g.,
2021-07 → 2023-06: bull top → bear → recovery) to bound compute, recording
PF, Sharpe, Sortino, MaxDD, Expectancy, #trades, and median holding time at
each point. Outputs:
- **Sensitivity curves** — metric vs perturbation-% per threshold.
- **Tornado chart** — thresholds ranked by metric swing (most influential knobs).
- **2-D surfaces (heatmaps)** — the 2–3 most influential interacting pairs
  (e.g., `adx_min_1h × min_score`, `atr_stop_multiplier × reward_atr_multiple`).

**Interpretation:**
- **Accept = plateau:** graceful degradation; key metrics stay above §7
  minimums across ±10% (ideally ±20%); the default sits near the centre of a
  broad stable region.
- **Reject = cliff:** a ±5% or ±10% move collapses or flips a key metric —
  the default is an overfit spike.
- One-at-a-time misses interactions ⇒ the 2-D surfaces and Monte-Carlo (§14)
  supplement it. The chosen config is then re-confirmed on full OOS.

## 14. Monte Carlo + confidence intervals

From the realized OOS trades, resample **N = 10,000** times; report **90%
and 95% CIs** for **CAGR, Sharpe, Max Drawdown, Expectancy** (plus PF).

- **iid bootstrap** (resample trade returns with replacement) → CIs for
  Expectancy / Sharpe / PF.
- **Stationary/block bootstrap** (preserves short-run autocorrelation) →
  CIs for Max Drawdown and CAGR (path-dependent).
- Separate **randomized-entry** MC as a null benchmark (the edge must beat it).

**How intervals drive acceptance:**
- Gate on the **lower CI bound** for "good = high" metrics (Sharpe
  lower-95% ≥ 0.7; Expectancy lower-95% > 0; PF lower-95% ≥ 1.15) and the
  **upper CI bound** for Max Drawdown (upper-95% ≤ 30%).
- Wide CIs straddling a minimum ⇒ "not robustly established" ⇒ inconclusive
  = fail, even if the point estimate passes.
- Position-size and risk assumptions are set against the 95th-percentile
  simulated drawdown and worst plausible losing streak, not the realized path.

## 15. Explicit stress tests

| Scenario | Method | Expected behaviour / pass criterion |
|---|---|---|
| **Double fees** | Re-backtest with `--fee 0.002` (0.4% round-trip). | Metrics degrade but stay net-positive: PF ≥ 1.15, expectancy > 0. Flipping to a loss ⇒ edge too thin ⇒ fail. |
| **Added slippage** | Haircut each trade by +5–10 bps/side (post-process the export) or a custom price model (tooling). | Graceful degradation; must survive ~5–10 bps extra per side. Short holds are slippage-sensitive — critical test. Freqtrade slippage modelling is approximate; documented. |
| **Sudden volatility spike** | Backtest windows containing known shocks (2020-03-12, 2021-05-19, 2022-11 FTX, later spikes) ± a synthetic gap. | 6% hard stop caps loss under normal fills; gaps can exceed it (documented). Max single-trade loss and window drawdown stay within limits; no over-trading into the spike. |
| **Exchange downtime / delayed exits** | Post-process assuming exits fill 1–3 candles (5–15 min) late at the then-price; optionally skip a data window. | Objective-signal exits tolerate small delays; stop exits degrade more. Quantify the hit; must stay within the risk-adjusted envelope. |
| **Consecutive losing streak** | Realized + MC max-losing-streak distribution; stress the 99th-percentile streak at full concurrent exposure. | With ~1% risk/trade × max 3 concurrent, an N-loss streak ≈ N% drawdown; capital survives the 99th-percentile streak; no streak breaches the MaxDD reject line. |

Overall stress pass: the strategy remains inside the §7 envelope under
2× fees + added slippage, and breaches no MaxDD limit under the spike and
streak stresses.

## 16. Portfolio-level validation

Trade-level metrics answer "is each trade good?"; this answers "is the book
of concurrent positions safe and efficient?" It applies now (BTC+ETH,
`max_open_trades: 3` — trades already overlap) and the same tooling scales
to the future multi-asset book. Computed by the portfolio-analysis script
(§24), which reconstructs from the exported trades (open/close timestamps,
stake, per-trade P&L, each trade's live stop) the time series of concurrent
positions and deployed capital.

| # | Metric | Definition | Acceptable range / flag | Influence on deployment |
|---|---|---|---|---|
| 1 | Avg simultaneous positions | Time-weighted mean of concurrently open trades. | Informational; < 0.5 ⇒ mostly idle; near cap ⇒ frequent saturation. | Signals whether `max_open_trades` and capital are right-sized. |
| 2 | Max simultaneous positions | Peak concurrent trades. | ≤ `max_open_trades` (else logic error → reject). | If the cap binds often, review raising it. |
| 3 | Portfolio exposure over time | Σ(open stakes)/capital as a series (mean, peak, distribution). | Peak ≤ `tradable_balance_ratio` (0.99). | Sustained high exposure ⇒ systemic risk; low ⇒ under-utilization. |
| 4 | Exposure by asset | Capital share per pair over time; peak per asset. | No asset dominates beyond a pre-set cap (~1/N future; ~50/50 expected now). | Sets per-asset stake caps against concentration. |
| 5 | Portfolio drawdown | Drawdown of the aggregate equity curve. | Must meet the §7 MaxDD gate (≤ 25%, upper-CI ≤ 30%). | THE deployment risk number; concurrent positions can co-draw-down. |
| 6 | Capital utilization | Average fraction of capital deployed (≈ mean exposure). | ~15–70% for a selective strategy; flag pinned ~0 or ~max. | Right-sizes capital allocated to the bot. |
| 7 | Idle capital % | 100% − utilization. | High is fine (cost of discipline); quantify opportunity cost. | Don't allocate more capital than the strategy can deploy. |
| 8 | Correlation between simultaneously held assets | Return correlation of concurrently held assets over their overlap window. | Report distribution; **flag persistent > 0.7** (illusory diversification). | Correlation-aware caps; highly correlated names share one risk budget. |
| 9 | **Worst-case simultaneous-stop loss** | max over time of Σ(open position size × current stop distance). | **≤ ~15% of capital; reject > 20%.** (3 slots × up to the 6% cap ⇒ naive worst ≈ 18% — jointly gates `max_open_trades × risk_per_trade × hard_stop_pct`.) | THE binding constraint on concurrent risk, since crypto correlations → 1 in crashes. |

**Deployment summary:** portfolio drawdown and worst-case simultaneous-stop
set capital allocation and the concurrent-risk budget; concurrent-holding
correlation sets correlation-aware caps (critical for the future multi-asset
book); utilization/idle right-size allocated capital; exposure-by-asset sets
per-asset caps. All ranges are pre-registered (adjustable only before OOS is
seen).

## 17. Gate 0 — lookahead-analysis & recursive-analysis

Run immediately after the smoke test / full download, before any performance
number is trusted:

- **`lookahead-analysis`** re-runs the strategy with and without access to
  future candles and flags any signal that changes — direct look-ahead bias.
  Must return **zero findings**. Run on a trade-dense slice with sufficient
  `--targeted-trade-amount`.
- **`recursive-analysis`** recomputes indicators at a fixed point while
  growing the startup window (`--startup-candle 199 499 999 1999 3600`);
  reports indicators whose past values drift — recursive/repainting
  indicators (ADX and EMA have unstable warmups). Confirms
  `startup_candle_count = 3600` is genuinely sufficient (values converged).

**Required overlay (Phase A discovery, 2026-07-14).** Both commands MUST be
run with an additional `--config user_data/config_analysis.json` appended
after the base config. These two tools force market orders internally, which
Freqtrade rejects unless `entry_pricing`/`exit_pricing` `price_side` is
`"other"`; the overlay sets only that and applies to these analysis commands
alone. It does not affect trading, backtesting, the methodology, the
acceptance criteria, or any threshold.

Failure voids all downstream numbers until the code is fixed and both re-run
clean. Both are re-run after any strategy-layer change.

## 18. Pipeline Smoke Test (first executable phase)

Before committing to the multi-year, multi-pair download, prove the entire
pipeline on a tiny sample:

- Download **BTC/USDT only, all timeframes**, a short window with pre-roll
  (e.g., **2024-02-01 → 2024-06-30**, analysis from 2024-03-01).
- Run end-to-end: **download → data-integrity check → lookahead-analysis →
  recursive-analysis → backtest → report generation (§19)**.
- **Pass = plumbing works:** every stage runs without error, produces a
  valid report, and yields sane numbers (trades > 0, holding time in the
  right ballpark, no bias findings).
- Explicitly **not** a performance judgment — the strategy is never
  accepted or rejected on smoke results. Only after it passes does the full
  historical download proceed.

## 19. Standardized Markdown report

Every completed backtest auto-generates one Markdown report (companion
report-generator script, §24) from Freqtrade's summary + `--export trades`
+ `trade_rejections.jsonl` + the regime/sensitivity/MC/stress/portfolio
tooling. Fixed sections:

| # | Section | Contents / source |
|---|---|---|
| 1 | Executive Summary | Verdict, headline risk-adjusted metrics, one paragraph. |
| 2 | Configuration Used | Full `algo_trader` config, Freqtrade version, fee/slippage assumptions, exact command. |
| 3 | Data Window | Pairs, timeframes, timerange, candle counts, download date/hash. |
| 4 | Validation Gates | Gate 0 (§17) + smoke test (§18) pass/fail. |
| 5 | Performance Metrics | CAGR, Sharpe, Sortino, PF, win rate, expectancy, #trades, Calmar. |
| 6 | Risk Metrics | MaxDD abs/%, DD duration, Recovery Factor, exposure, worst trade. |
| 7 | Portfolio Analysis | §16 battery: exposure/utilization series, concurrent-correlation matrix, worst-case simultaneous-stop. |
| 8 | Holding-Time Distribution | §12 percentile table + histogram, per exit reason. |
| 9 | Monthly Returns | Month-by-month table/heatmap (`--breakdown month`). |
| 10 | Drawdown Analysis | Top-N drawdowns (depth/duration/recovery), underwater curve. |
| 11 | Exit Reason Analysis | Counts/P&L by exit tag (stop vs signal) via `backtesting-analysis`. |
| 12 | GO/NO-GO Rejection Statistics | From `trade_rejections.jsonl`: counts by category, top reasons, GO:NO-GO ratio, most-binding gate. |
| 13 | Regime Breakdown | §11 trend×vol matrix + five named-regime metrics. |
| 14 | Sensitivity Analysis | §13 tornado + curves + stable-region verdict. |
| 15 | Stress-Test Results | §15 per-scenario metric deltas + pass/fail. |
| 16 | Monte Carlo Results | §14 CIs for CAGR/Sharpe/MaxDD/Expectancy + distributions. |
| 17 | Final Verdict | PASS/FAIL against §7, listing which gates passed/failed. |
| 18 | Recommended Next Action | Proceed / iterate (what to change) / shelve. |

Mode B reports additionally append a **Retraining-Cadence Comparison**
section reporting the §10.1 scoring across Monthly/Quarterly/Semi-annual.

Reports land in `user_data/backtest_results/reports/` (git-ignored); the
Executive Summary + Final Verdict are copied into `docs/LEARNINGS.md`, and
any threshold decision into `docs/DECISIONS.md`.

## 20. RegimeDetector — future validation methodology

The module returns `TREND` today (enum: `TREND, RANGE, HIGH_VOLATILITY,
LOW_VOLATILITY, UNKNOWN`). When real classification is implemented, it is
validated against the objective §11 benchmark labels as ground truth,
lookahead-safe, **before** any regime-based routing is enabled:

| Measure | Method | Provisional bar |
|---|---|---|
| Classification accuracy | Confusion matrix vs §11 labels; per-class precision/recall/F1 + overall. | Overall agreement ≥ 70%; per-class recall ≥ 60%. |
| % UNKNOWN | Share of candles classified UNKNOWN (excluding warmup). | ≤ 10–15% steady-state. |
| Regime stability | Run-length (dwell-time) distribution per regime. | Median dwell ≥ a meaningful horizon (e.g., ≥ 12h); no candle-to-candle flicker. |
| Transition frequency | Regime changes per unit time vs the benchmark's rate. | Within ~1.5× the benchmark transition rate. |
| False regime switches | A→B→A reversions within K candles (whipsaws). | Below a set ceiling; mitigate with hysteresis/confirmation. |
| Detection latency | Lag between benchmark regime change and detector recognition. | Reported; small and consistent. |

## 21. Validation flowchart

```
 ┌──────────────────────────────────────────────────────────────────┐
 │ SMOKE TEST (§18)  BTC/USDT, 2024-02→2024-06, ALL timeframes        │
 │  download → integrity → lookahead → recursive → backtest → REPORT  │
 └───────────────┬───────────────────────────────── FAIL → fix tooling┘
                 ▼ PASS (plumbing only)
 ┌──────────────────────────────────────────────────────────────────┐
 │ FULL DATA ACQUISITION  1m/5m/15m/1h/4h/1d, BTC+ETH, 2019-12→2026-06│
 └───────────────┬──────────────────────────────────────────────────┘
                 ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │ GATE 0 (§17)  lookahead (0 findings) + recursive (stable @ 3600)   │──FAIL→fix code
 └───────────────┬──────────────────────────────────────────────────┘
                 ▼ PASS
 ┌──────────────────────────────────────────────────────────────────┐
 │ BASELINE BACKTEST (fixed defaults, 1m detail, real fees)          │
 │  sanity: trades > 0, holding-time IQR in band                     │
 └───────────────┬──────────────────────────────────────────────────┘
                 ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │ WALK-FORWARD Mode A (§10) → concat OOS                            │
 │   → REGIME BREAKDOWN (§11)  → HOLDING-TIME DIST (§12)             │──FAIL→reject/redesign
 └───────────────┬──────────────────────────────────────────────────┘
                 ▼ PASS
 ┌──────────────────────────────────────────────────────────────────┐
 │ ROBUSTNESS:  Sensitivity ±5/±10/±20 (§13)                         │
 │              Monte-Carlo CIs (§14)                                │──FAIL→reject
 │              Stress tests (§15)                                   │
 │              Portfolio-level analysis (§16)                       │
 └───────────────┬──────────────────────────────────────────────────┘
                 ▼ PASS
 ┌──────────────────────────────────────────────────────────────────┐
 │ LOCKED HOLDOUT (one look, 2025-07→2026-06) → §7 risk-adjusted     │──FAIL→reject
 └───────────────┬──────────────────────────────────────────────────┘
                 ▼ PASS
 ┌──────────────────────────────────────────────────────────────────┐
 │ CROSS-PAIR OOS  8 unseen pairs, locked config → ≥ 6/8 edge        │──FAIL→reject
 └───────────────┬──────────────────────────────────────────────────┘
                 ▼ PASS
 ┌──────────────────────────────────────────────────────────────────┐
 │ DECISION + STANDARDIZED REPORT (§19) → LEARNINGS.md / DECISIONS.md │
 └──────────────────────────────────────────────────────────────────┘
   (Optional later: Mode B reoptimizing walk-forward + cadence study §10.1)
```

## 22. Command sequence (post-approval; not yet executed)

Windows-safe form: prefix `MSYS_NO_PATHCONV=1`, container-relative config
path. Steps marked **[tooling]** require the §24 companion scripts.

```bash
# ── Phase A: SMOKE TEST (small sample, full pipeline) ──
MSYS_NO_PATHCONV=1 docker compose run --rm freqtrade download-data \
  --config user_data/config.json --exchange binance \
  --pairs BTC/USDT --timeframes 1m 5m 15m 1h 4h 1d --timerange 20240201-20240630

MSYS_NO_PATHCONV=1 docker compose run --rm freqtrade lookahead-analysis \
  --config user_data/config.json --config user_data/config_analysis.json \
  --strategy AdaptiveTrendStrategy \
  --timerange 20240301-20240630 --targeted-trade-amount 50

MSYS_NO_PATHCONV=1 docker compose run --rm freqtrade recursive-analysis \
  --config user_data/config.json --config user_data/config_analysis.json \
  --strategy AdaptiveTrendStrategy \
  --timerange 20240301-20240401 --startup-candle 199 999 3600

MSYS_NO_PATHCONV=1 docker compose run --rm freqtrade backtesting \
  --config user_data/config.json --strategy AdaptiveTrendStrategy \
  --timeframe-detail 1m --fee 0.001 --timerange 20240301-20240630 \
  --export trades --export-filename user_data/backtest_results/smoke.json
# [tooling] generate_report.py smoke.json  → verify pipeline only

# ── Phase B: FULL DATA (only after smoke passes) ──
MSYS_NO_PATHCONV=1 docker compose run --rm freqtrade download-data \
  --config user_data/config.json --exchange binance \
  --pairs BTC/USDT ETH/USDT --timeframes 1m 5m 15m 1h 4h 1d \
  --timerange 20191201-20260630

# ── Phase C: GATE 0 on real windows ──
MSYS_NO_PATHCONV=1 docker compose run --rm freqtrade lookahead-analysis \
  --config user_data/config.json --config user_data/config_analysis.json \
  --strategy AdaptiveTrendStrategy \
  --timerange 20220101-20221231 --targeted-trade-amount 200

MSYS_NO_PATHCONV=1 docker compose run --rm freqtrade recursive-analysis \
  --config user_data/config.json --config user_data/config_analysis.json \
  --strategy AdaptiveTrendStrategy \
  --timerange 20240101-20240401 --startup-candle 199 499 999 1999 3600

# ── Phase D: baseline + walk-forward Mode A ──
MSYS_NO_PATHCONV=1 docker compose run --rm freqtrade backtesting \
  --config user_data/config.json --strategy AdaptiveTrendStrategy \
  --timeframe-detail 1m --fee 0.001 --breakdown month \
  --timerange 20200101-20250630 \
  --export trades --export-filename user_data/backtest_results/dev_baseline.json
# loop the ~18 OOS quarters (20210101-20210401, … step +3 mo), same config,
# each exported to its own file

# ── Phase E: robustness [tooling] ──
# sweep_sensitivity.py  (±5/10/20% per threshold → curves, tornado, heatmaps)
# monte_carlo.py        (10k resamples → CIs for CAGR/Sharpe/MaxDD/Expectancy)
# stress_tests.py       (2× fees, +slippage, vol-spike windows, delayed exits, streaks)
# portfolio_analysis.py (§16 battery from exported trades)

# ── Phase F: locked holdout (ONCE) + cross-pair ──
MSYS_NO_PATHCONV=1 docker compose run --rm freqtrade backtesting \
  --config user_data/config.json --strategy AdaptiveTrendStrategy \
  --timeframe-detail 1m --fee 0.001 --breakdown month \
  --timerange 20250701-20260630 \
  --export trades --export-filename user_data/backtest_results/holdout.json

MSYS_NO_PATHCONV=1 docker compose run --rm freqtrade backtesting \
  --config user_data/config.json --strategy AdaptiveTrendStrategy \
  --timeframe-detail 1m --fee 0.001 \
  --pairs SOL/USDT BNB/USDT XRP/USDT ADA/USDT DOGE/USDT LINK/USDT AVAX/USDT LTC/USDT \
  --timerange 20250701-20260630 \
  --export trades --export-filename user_data/backtest_results/crosspair.json

# ── Phase G: analysis + report ──
MSYS_NO_PATHCONV=1 docker compose run --rm freqtrade backtesting-analysis \
  --config user_data/config.json \
  --analysis-groups 0 1 2 --enter-reason-list all --exit-reason-list all
# [tooling] generate_report.py <each export> → §19 report → LEARNINGS/DECISIONS
```

## 23. Reproducibility & realistic assumptions

Pin Freqtrade `2026.6`; record data download date/hash and the exact
`algo_trader` config per run. `user_data/backtest_results/` is git-ignored,
so summary metrics + verdicts are written into `docs/LEARNINGS.md` and
threshold decisions into `docs/DECISIONS.md`. Fees ≥ 0.1%/side (Binance spot
taker; ~0.2% round-trip is a heavy tax on 30–120 min holds — gross-positive/
net-negative is an automatic reject). `--timeframe-detail 1m` for fill
realism. Known optimism: the order-book spread gate self-disables in
backtesting (live rejects more entries); spot ⇒ no funding costs;
survivorship negligible for these still-listed majors.

## 24. Companion tooling to be built (separate approved coding tasks)

1. **Regime classifier + bucketing** (§11) — daily trend/vol labels, per-trade tagging, per-regime metrics.
2. **Sensitivity sweep** (§13) — ±5/10/20% driver; tornado/curve/heatmap output.
3. **Monte-Carlo CI** (§14) — iid + block bootstrap; CI computation.
4. **Stress-test harness** (§15) — fee/slippage/vol-spike/delay/streak scenarios.
5. **Standardized report generator** (§19) — the 18-section Markdown.
6. **Portfolio-analysis script** (§16) — concurrent positions/exposure/correlation + worst-case simultaneous-stop from exported trades.
7. **Retraining-cadence harness** (§10.1, Mode B) — run and score Monthly/Quarterly/Semi-annual walk-forwards.
8. **(Future) RegimeDetector metrics** (§20) — accuracy/stability/transition/false-switch measurement.

## 25. Frozen decisions (approved 2026-07-14)

1. **Unseen-pair set:** SOL, BNB, XRP, ADA, DOGE, LINK, AVAX, LTC (/USDT).
2. **Regime definitions:** daily EMA50/ADX≥20 trend axis; 30/70 trailing
   365-day ATR% percentiles for the volatility axis (§11).
3. **Acceptance criteria:** the §7 risk-adjusted table (CAGR de-gated),
   pre-registered.
4. **Walk-forward order:** Mode A (fixed-parameter) first; Mode B deferred
   and requires a separate approval + parameter-exposure task.
5. **Retraining-cadence prior:** Quarterly, overridden only by the §10.1
   objective comparison.
6. **Portfolio ranges:** worst-case simultaneous-stop ≤ 15% of capital
   (reject > 20%); utilization band ~15–70% (§16).

---

## 26. Benchmark-aware acceptance (AMENDMENT, approved 2026-07-17, D-031)

**Why the amendment.** Phase 9 (L-010) showed the pre-registered §7 gate,
designed and validated at short (8-bar / intraday) horizons, can be cleared at
multi-week horizons by market participation alone: 2 of 3 seeded random-entry
controls PASSed on the real NSE corpus, because at 40–60-day horizons absolute
forward return is dominated by bull-market drift and the survivorship of
measuring today's constituents. Absolute return is therefore not a sufficient
promotion criterion. This amendment adds a benchmark-relative gate. It does NOT
relax any existing threshold — it adds a requirement.

**26.1 Baselines (every measured strategy, identical trade constraints).**
Each is deterministic (seeded) and priced through the SAME risk engine, costs
and intrabar convention as the strategy:

1. **Buy & Hold, matched per trade** — same symbol, same entry bar, held the
   strategy's full `max_hold_bars`, net one round trip. Isolates entry+exit
   skill from being long the picked name.
2. **Buy & Hold, portfolio** — equal-weight the traded universe over the
   window. The "just be long the market" number. (Generous by survivorship —
   deliberately, so beating it means more.)
3. **Random entry, K≥3 deterministic seeds** — random (symbol, bar) entries,
   matched to the strategy's trade count, natural managed exits.
4. **Random entry, matched holding period** — as (3) but exits forced to the
   strategy's mean realized holding, removing the exit engine from the
   comparison.

**26.2 The binding gate — Selection Edge.** At the entry level, the SELECTION
edge is the strategy's mean forward return minus a random entry's (baseline 3,
the corpus drift). Its 95% lower bound uses the D-028 block bootstrap on the
DIFFERENCE (both samples resampled by independent day-blocks). Evaluated at the
horizon that maximises the selection edge; the identical rule applied to the
random-entry control — which has no selection edge at any horizon — is what
keeps that choice honest.

> **Promotion requires the selection-edge CI lower bound to exceed the
> round-trip cost**, exactly as D-007 required of the absolute edge. A strategy
> that clears every absolute §7 bar but shows no selection edge (CI-low ≤ 0) is
> earning market drift, not skill, and is **FAILed**. The absolute §7 bars are
> retained in full: a promotable strategy must clear BOTH.

**26.3 Reported comparative metrics (with CIs where valid):** Selection Edge;
Excess Return vs Buy & Hold; Excess Return vs Random; Information Ratio (per-
trade excess vs matched B&H, annualized, where tracking error is non-trivial and
n≥30); Relative Profit Factor (strategy ÷ random); Relative Drawdown (strategy −
random). These enrich the evidence; the binding gate is 26.2.

**26.4 Assumptions & limitations (documented, not hidden).**
- The random baseline is drawn UNIFORMLY across dates, so it removes overall
  drift but not drift *concentration* (a strategy trading mostly in the bull
  sub-period keeps some timing benefit). A same-day cross-sectional baseline
  would remove that too but cannot be earned by a per-symbol strategy that is
  the only one trading on a date; it is a documented future tightening, not a
  current gate.
- The random baseline's own sampling error is included via the two-sample
  bootstrap (it is not treated as a fixed constant).
- Buy & Hold uses the current universe over the whole window (survivorship);
  this makes it a generous drift benchmark on purpose.
- Everything is deterministic under the declared seeds and replayable.
