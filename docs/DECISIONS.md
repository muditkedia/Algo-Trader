# Decisions

Architecture and strategy decisions, with the evidence behind them. Newest first.

---

## D-011 — Phase 1 platform foundation built (2026-07-15)

Built the reusable, market-agnostic foundation under `src/algo/` (see
docs/PROJECT_STATE.md): core primitives, the SQLite evidence store + logger, the
strategy plugin interface + registry, the universe-filter framework, the scanner
interface, and the research-engine skeleton. No strategies, brokers, data, or
trading — foundation only, by the approved plan. 32 pytest tests + the validation
self-test + an on-disk startup smoke all pass. Uncommitted pending owner review.

## D-010 — Evidence database is SQLite; market data stays in parquet (2026-07-15)

The evidence store (every signal, outcome, trade, evaluation, regime label, run)
is a single SQLite file — one machine, one writer, analytics not OLTP, pandas-
friendly, transactional, keepable forever. Candles are NEVER stored there; they
stay in parquet. This division keeps the evidence DB small enough to retain
indefinitely, which is the point (evidence is the product). No server database —
that would be speculative infrastructure.

## D-009 — Retire Freqtrade; adopt an `src/algo` package; archive crypto (2026-07-15)

Freqtrade is structurally crypto-only (CCXT brokers, no NSE path, no session
calendar) so it is retired, not adapted. The project moves to a standard
src-layout Python package (`src/algo`, editable install, pytest), and the crypto
code is preserved under `archive/crypto-freqtrade/` — nothing deleted. The
market-agnostic reuse candidates (indicators, risk_engine, trade_manager,
decision_engine) are flagged there for Phase-2 promotion. The validation package
was relocated into the platform and decoupled from `algo_core`.

## D-008 — Pivot to a research-first Indian equities platform (2026-07-15)

Owner-approved pivot from crypto to Indian equities, with the architecture
centered on **discovering** profitable opportunities (the Research Engine +
evidence database) rather than implementing preconceived strategies. Rationale:
D-007 — the crypto project engineered an excellent platform around an edge that
was never large enough. The new design makes measurement-before-implementation
an architectural gate, not a discipline. Full design approved across the
repository-audit, research-engine, and phase-1 design exchanges.

---

## D-007 — Stop strategy iteration; entry signal must be replaced (2026-07-14)

**Evidence (LEARNINGS L-009):** the entry edge was measured directly over 1,699
entries with exits ignored. It is real and statistically significant (gross
+13.3 bps at 180 min, 95% CI [+3.7, +22.7]; MFE/|MAE| 1.17 vs 0.97 random) but
**never exceeds the 20 bps round-trip cost at any horizon**, and the median
entry is negative even before fees.

**Decision: stop iterating on exits/risk/portfolio.** Three exit designs, two
full redesigns and a controlled single-variable experiment have all confirmed
the same arithmetic: gross expectancy ~3 bps/trade vs 20 bps cost. This is a
signal-strength problem, not a calibration problem.

**Options (owner decision required, none taken):**
1. Replace the entry signal with one whose gross edge exceeds ~2x costs.
2. Re-scope holding horizons far beyond the frozen SS7 30-120 min band - the
   edge grows with horizon (1.5 -> 13.3 bps over 30 -> 180 min) and may only
   clear costs at multi-hour/day holds. Requires a protocol amendment.
3. Shelve.

## D-006b — Gate 0 blocker resolved (2026-07-14)
v3's lookahead flag proven a Freqtrade tool artifact, not a strategy bias
(LEARNINGS L-008). All v1/v2/v3 results are valid. A latent robustness flaw is
recorded but deliberately NOT fixed under the analysis-only mandate:
`populate_indicators` silently drops columns when informative data is missing,
which suppresses signals and breaks freqtrade's bias tooling.

---

## D-005 — AdaptiveTrend v3: controlled exit refinement (2026-07-14)

Single-variable change vs v2: replaced the 5m EMA-cross structural exit with a
**confirmation-based 15m exit** (`ema_fast_15m < ema_slow_15m` AND
`close < ema_slow_15m`). Everything else from v2 held constant (anti-chase,
cost gate, tighter trailing, removed advisory scoring + RR gate). v1 and v2
left completely intact; all three strategies load side by side. No tuning, no
hyperopt - the exit reuses existing 15m EMAs with no new thresholds.

**Reasoning:** 15m is 3x coarser than 5m so routine pullbacks that hold above
the 15m structure cannot trigger it; requiring price below the 15m slow EMA is
the confirmation, so it only fires on a genuine structural break.

**Outcome - partially validated, design goal missed:**
* **Fixed the v2 catastrophe:** structural fires 1,469 -> 63; net -897 -> -82;
  overall -548 -> -421; PF 0.47 -> 0.60; win 32.4% -> 54.8%; MaxDD 55% -> 43%.
* **Missed its goal:** it does NOT exit earlier than the 1h version - it fires
  at a 605-min lag (vs v1's 250 min), because a full 15m death-cross plus price
  confirmation is a slow condition.
* **All three structural exits (1h/5m/15m) have a 0% win rate.** -> D-006.

## D-006 — Structural exits to be REMOVED in v4 (2026-07-14)

Evidence: three structural-exit variants across three timeframes have now been
tested; every one is net-negative with a **0% win rate** (1h: -56, 5m: -897,
15m: -82). None has ever produced a winning trade. The trailing stop is the only
profitable exit (v3: +553 USDT, 80% win). **Decision: v4 removes structural
exits entirely; the trailing stop + hard stop become the only exits.**

Caveat recorded: this alone will not make the strategy profitable. L-006 shows
all three versions are gross-flat before fees (per-trade gross edge ~3 bps vs
20 bps cost). v4 must also confront the cost/edge gap and the trail/stop
asymmetry (avg win +0.48 vs avg loss -1.03).

---

## D-004 — AdaptiveTrend v2 redesign (2026-07-14)

Built `AdaptiveTrendStrategyV2` alongside the intact v1, changing only the four
subsystems Phases D/E proved ineffective. Each decision and its validated
outcome:

| Change | Rationale (evidence) | Validated outcome |
|---|---|---|
| **Remove advisory scoring** (RSI/volume/volatility weights) | Phase E: winner score == loser score (AUC 0.51); no predictive value | Kept removed - no regression |
| **Remove risk_reward gate** | Phase E: near-constant ~1.5 (74.6% of trades), corr with outcome ~0.07 | Kept removed - no regression |
| **Add anti-chase gate** (reject `dist_fast_5m` > 0.3% or `mom_1h` > 1.5%) | Phase E: chasing extended moves harmful (AUC ~0.45) | **Helped** - initial stop-outs fell 314 -> 49 |
| **Add cost gate** (expected reward = 2xATR% must exceed 2x round-trip cost) | Phase E: entry edge ~17bps < 20bps cost; observed MFE ~2x ATR% | Neutral-to-helpful (part of the stop-out reduction) |
| **Tighter trailing** (1.25xATR @ +0.4%, lock ~50% of each tier) | Phase D: v1 captured only 43% of MFE, gave back 1.26%/trade | **Helped** - trailing win 72% -> 78.7%, +274 -> +421 USDT |
| **Replace 1h-EMA exits with 5m EMA-cross reversal exit** | Phase D: v1 objective exits lagged ~225min behind the peak | **FAILED CATASTROPHICALLY** - see D-004a |

### D-004a — The 5m EMA-cross exit hypothesis was wrong
Replacing the too-slow 1h exit with a 5m fast/slow EMA-cross reversal exit
**over-corrected into noise**: it fired 1,469 times at a **0.1% win rate** for
**-897 USDT**, ejecting positions on routine 5m pullbacks before either the
trend resumed or the trailing stop could lock profit. It also collapsed mean
MFE (1.15% -> 0.65%) by cutting trades before they developed. Net v2 result:
**worse than v1** (-54.8% vs -15.2%, MaxDD 55% vs 16%) despite two of the four
changes being beneficial. Kept in the repo as a recorded negative result; v3
must remove it and let the (tightened) trailing stop be the primary exit.

### Preserved (unchanged, proven valuable / structural)
MTF pipeline, indicator calculations, mandatory GO architecture, hard stop,
position sizing, the trailing MECHANISM, RegimeDetector, validation tooling.
v1 left completely intact (self-test still 84/84 PASS; Gate 0 still clean).

### Method note
All v2 values are hand-set from Phase D/E evidence and documented; **no
hyperopt, no parameter optimization** was used (per task constraint).

---

## D-003 — Validation protocol frozen (2026-07-14)
The historical-data + backtesting methodology was frozen as
`architecture/VALIDATION_RULES.md` (25 sections). Acceptance is risk-adjusted
(no fixed CAGR floor). Amendment requires explicit approval.

## D-002 — Risk-based sizing disabled by default (2026-07-13)
`RiskParams.enable_risk_sizing = False`; the strategy uses the fixed config
stake until sizing is validated. Enabling requires `stake_amount: "unlimited"`.

## D-001 — Single source of truth for thresholds (2026-07-13)
Every tunable lives in `algo_core/settings.py`; profile and engine read the
same params. Established after an audit found duplicated profile/engine
constants.
