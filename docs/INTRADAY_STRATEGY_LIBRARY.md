# INTRADAY STRATEGY LIBRARY

_Written 2026-07-18 (project reset: practical automated intraday trading system).
Documentation only — no code was modified to produce this document._

This document describes **every strategy currently implemented in this
repository**: what it is, how it enters and exits, its stop rules, the timeframe
it trades, where it is implemented, and whether it is complete. It also contains
the coverage table against the ~20 target intraday strategies (§5).

**Inventory summary:** the strategy library (`src/algo/strategies/library/`)
registers **35 strategies** via auto-discovery:

| Group | Count | Timeframe | Holding | Relevant to the intraday objective? |
|---|---|---|---|---|
| Intraday | **12** | 11 × 15m, 1 × 1h | same-day (MIS) | **Yes — these are the intraday library** |
| Daily swing / positional | 23 | 1d | days–months (CNC) | No (documented for completeness; they hold overnight) |

_2026-07-19: five strategies added (batch 2, §2.8–2.12): `gapgo_15m`,
`insidebar_15m`, `supertrend_15m`, `cpr_reversal_15m`, `nr7_intraday_15m` —
canonical forms, each owning its execution declaration, not yet backtested._

**Data available in the store** (`user_data/data/nse/`):

| Timeframe | Symbols | Coverage | Bars |
|---|---|---|---|
| 5m | NIFTY-100 + microcap tier + context series | equities 2016-10-03 → now | ~17.6 M+ |
| 15m | NIFTY-100 + BANKNIFTY/INDIAVIX | 2016-10-03 → now (NIFTY50 15m pending remediation) | ~5.8 M |
| 1h | 99 (NIFTY-100) | 2023-01-02 → now | ~0.60 M |
| 1d | NIFTY-500 + context | 2015-01-01 → now | ~1.1 M |

> Current state of the store is maintained in `docs/DATA_CAPABILITY_REPORT.md`
> (updated after each acquisition); the table above is a snapshot.

---

## 1. Execution model — strategy-owned (project-reset design change, 2026-07-18)

**Every strategy now owns its complete execution declaration.** Each strategy
module carries an `execution` attribute (an `algo.execution.ExecutionSpec`)
that fully defines its entry timing, initial stop-loss, profit target(s),
optional partial exit, trailing rules, session restrictions, maximum holding
period and square-off logic. The execution engine
(`src/algo/execution/engine.py`) is strategy-agnostic: it interprets and
executes that declaration and imposes nothing of its own. There is no
universal stop-loss, no universal trailing stop, and no universal exit.
Strategies whose intended behaviour is identical share spec constructor
helpers (`structural_intraday`, `atr_trail_intraday`, `atr_trail_swing`), but
the declaration lives in each strategy's own module.

Rules common to every declaration currently in the library:

- **Entry execution:** at the **close of the bar on which the signal fires**
  (all entry conditions are close-confirmed crossovers; trigger-level resting
  orders were measured in the Phase-17 fidelity audit to carry an information
  advantage that bar data cannot honestly fill, so signal-close is the
  implementable entry timing every strategy declares).
- **Session restrictions (intraday):** no entry on the session's last bar; a
  position never crosses a session boundary; square-off at the session's
  actual last bar close. **No overnight carry, ever** (no strategy declares
  overnight support).
- **Holding limit:** intraday positions run to the strategy's own exit or the
  session square-off — there is **no generic bar cap**. Swing strategies
  declare their own `max_hold_bars` horizon.
- **Fills:** honest gap handling — a bar that opens beyond the stop (or
  target) fills at the open, not at the level. Same-bar ambiguity keeps the
  conservative convention: stop before target.
- **Costs:** every simulated P&L is **net** of the full NSE intraday charge
  stack (`NseEquityCostModel`): brokerage (0.03%, capped ₹20/order), STT
  (sell side), exchange txn, SEBI, stamp (buy side), GST, plus 2 bps/side
  slippage — ≈ **14.6 bps round trip** at ₹50k/trade.

Per-strategy declarations (the five with published exits recorded in
`research/INTRADAY_PRODUCTION_BATCH1.md` / the Phase-17 fidelity audit use
them; the other two declare their pre-reset profile as their own):

| Strategy | Initial stop (own) | Target (own) | Trailing (own) |
|---|---|---|---|
| `orb_15m` | below the opening-range low | OR high + 1× range | none |
| `vwap_15m` | the dip's low (4-bar session low) | 2R | none |
| `vwap_pullback_15m` | just below VWAP (level at entry) | 2R | none |
| `cpr_breakout_15m` | below the CPR bottom | floor-pivot R1 (50% partial, stop→breakeven) then R2 | none |
| `first_pullback_15m` | below the first pullback's low | 2R | none |
| `pullback_15m` | wider of 2×ATR(14) / 10-bar swing low, 6% cap | none | chandelier 2×ATR + profit-lock ladder |
| `volexp_1h` | wider of 2×ATR(14) / 10-bar swing low, 6% cap | none | chandelier 2×ATR + profit-lock ladder |
| `gapgo_15m` | below the first bar's low | 2R | none |
| `insidebar_15m` | below the inside bar's low | 2R | none |
| `supertrend_15m` | at the Supertrend line | none (ride the state) | the Supertrend line itself (`column` trail) |
| `cpr_reversal_15m` | below the rejection bar's low | the central floor pivot | none |
| `nr7_intraday_15m` | below the NR7 day's low | none (expansion-day ride) | none |

### Historical note — the pre-reset uniform model and its bugs (fixed)

Until 2026-07-18, exits were platform-uniform (D-006: ATR/structure stop,
chandelier trail, square-off) and the research simulator imposed them on every
strategy. Three implementation bugs were confirmed in that path and are fixed
in the strategy-owned engine (the frozen research simulator itself is kept
unchanged so recorded research verdicts stay reproducible):

1. **Entries on the session's final bar carried overnight** — now untradeable.
2. **A generic 8-bar holding cap** closed most trades ~2h after entry instead
   of the strategy's own session exit — removed; the session governs.
3. **Optimistic stop fills** booked gap-through stops at the stop price — now
   filled honestly at the open.

Still true in the corrected engine, by design: **close-of-bar entry** (the
signal conditions on the close — the Phase-16/17 audits measured the resulting
entry chase at +14 to +44 bps vs the unimplementable trigger price), and
**every signal simulated independently at fixed stake** (no portfolio netting,
no capital constraint, fractional share quantities).

---

## 2. Intraday strategies (12) — the current intraday library

### 2.1 Opening Range Breakout — `orb_15m`

1. **Strategy name:** Opening Range Breakout (ORB), volume-confirmed.
2. **Theory:** the first minutes of the NSE session establish the day's initial
   auction range; a later break of that range's high on elevated volume marks
   initiative buying that tends to persist through the session.
3. **Entry conditions:** after the 15-minute opening range has formed, the bar's
   **close crosses above the opening-range high** AND the breakout bar's volume
   is ≥ **1.5× its 20-bar average** (volume confirmation is mandatory, part of
   the entry). Long-only; at most one signal per stock per session in practice.
4. **Exit conditions (owned):** its published form — profit target at
   OR high + 1× the opening range; otherwise the stop; otherwise square-off at
   the session's last bar. Never overnight.
5. **Stop-loss rules (owned):** below the opening-range low (structural).
6. **Trailing stop rules (owned):** none.
7. **Timeframe:** 15m bars, NSE 09:15–15:30 session.
8. **Intended market conditions:** trending or range-expansion days
   (declared regimes: bull, range). Known to fail on open-drive days where no
   clean range forms, midday lunch-fade breaks, and wide/gappy opening ranges.
9. **Markets/stocks commonly used:** one of the most widely used intraday
   setups globally (Crabel lineage) and among Indian intraday traders on liquid
   F&O large-caps and index futures. Implemented universe here: 99 NIFTY-100
   cash equities.
10. **Current implementation status:** implemented (Phase 4), reused unchanged
    as production baseline #1 (Phase 15). Backtested on the full 15m store
    (Phase 7 D-026, Phase 15, Phase 17 fidelity). Evidence status `rejected`
    (does not clear the research cost gate); not paper-traded; no live path.
11. **Files:** `src/algo/strategies/library/orb_15m.py`; opening-range helper in
    `src/algo/core/indicators.py`; tests in `tests/` (ORB window/boundary and
    gap-handling tests); spec `research/INTRADAY_PRODUCTION_BATCH1.md` §1;
    reports `user_data/backtest_results/reports/intraday_production_batch1.md`,
    `fidelity_eval.md`.
12. **Complete or incomplete:** **complete** — entry signal plus its owned
    published-form execution declaration (stop below the OR low, 1×-range
    target), executed by the strategy-agnostic engine.

### 2.2 VWAP Trend Continuation — `vwap_15m`

1. **Strategy name:** VWAP Trend Continuation (VWAP reclaim in a buyer-controlled
   session). This is the library's "VWAP Trend" strategy.
2. **Theory:** institutions benchmark executions to session VWAP. On a session
   where price has spent most of its time above VWAP (buyers in control), a dip
   through VWAP that is immediately reclaimed is absorption, not distribution —
   the reversion to the institutional benchmark resolves in the trend's favour.
3. **Entry conditions:** ≥ **60%** of the session's *prior* bars closed above
   session VWAP, AND at least 4 bars into the session, AND the current bar
   **closes back above VWAP after trading below it** (an upward VWAP cross).
4. **Exit conditions (owned):** 2R profit target; otherwise the stop;
   otherwise square-off at the session's last bar. Never overnight.
5. **Stop-loss rules (owned):** below the dip's low (the 4-bar session low of
   the excursion the entry reclaims).
6. **Trailing stop rules (owned):** none.
7. **Timeframe:** 15m bars, session VWAP resets daily.
8. **Intended market conditions:** buyer-controlled trending sessions (declared
   regime: bull). Fails on late-session reclaims (no time before square-off),
   distribution-day first VWAP losses, and low-volume flat-VWAP drift.
9. **Markets/stocks commonly used:** VWAP strategies are standard among
   institutional and retail intraday traders on liquid NSE large-caps.
   Implemented universe: 99 NIFTY-100 equities.
10. **Current implementation status:** implemented (Phase 4), reused as
    production baseline #3 (Phase 15). Backtested (Phases 7/15/17 — its entry
    is close-based, so the fidelity audit found it the *only* faithful-entry
    baseline). Evidence status `rejected`; not paper-traded.
11. **Files:** `src/algo/strategies/library/vwap_15m.py`; `session_vwap` in
    `src/algo/core/indicators.py`; spec `research/INTRADAY_PRODUCTION_BATCH1.md`
    §3; same reports as §2.1.
12. **Complete or incomplete:** **complete** under its owned execution declaration.

### 2.3 VWAP Pullback — `vwap_pullback_15m`

1. **Strategy name:** VWAP Pullback (bounce off rising-VWAP support).
2. **Theory:** on a session trending up along a *rising* VWAP, a pullback whose
   low tags VWAP as dynamic support while the close never loses it is buyers
   defending the institutional benchmark — continuation at a discount. Distinct
   from `vwap_15m`: that buys the *reclaim* after a close below VWAP; this buys
   the *bounce* where the close never lost VWAP.
3. **Entry conditions:** close > session VWAP, AND VWAP rising bar-over-bar
   within the session, AND within the last 4 bars a bar's **low tagged VWAP**
   (≤ 0.2% above it) while that bar's close held above VWAP, AND at least
   4 bars into the session, AND the current bar **resumes** (close crosses
   above the prior bar's high).
4. **Exit conditions (owned):** 2R profit target; otherwise the stop;
   otherwise square-off at the session's last bar. Never overnight.
5. **Stop-loss rules (owned):** just below VWAP (the tagged support level,
   frozen at entry).
6. **Trailing stop rules (owned):** none.
7. **Timeframe:** 15m bars.
8. **Intended market conditions:** genuine rising-VWAP trend sessions (bull).
   Fails on distribution days (first tag breaks), flat/choppy VWAP, and
   late-session bounces.
9. **Markets/stocks commonly used:** classic discretionary intraday setup on
   liquid NSE names. Implemented universe: 99 NIFTY-100 equities.
10. **Current implementation status:** implemented new in Phase 15 (production
    baseline #2), backtested (Phases 15/17). The Phase-16 audit flagged that
    this encoding **over-fires 5–9×** relative to how a discretionary trader
    uses the setup (it is the highest-turnover strategy in the library:
    ~115k signals on the store). Evidence status `rejected`; not paper-traded.
11. **Files:** `src/algo/strategies/library/vwap_pullback_15m.py`; spec
    `research/INTRADAY_PRODUCTION_BATCH1.md` §2; reports as §2.1.
12. **Complete or incomplete:** **complete** under its owned execution
    declaration (with the over-firing caveat documented above).

### 2.4 CPR Breakout — `cpr_breakout_15m`

1. **Strategy name:** CPR Breakout (Central Pivot Range breakout on volume).
2. **Theory:** the CPR computed from the *prior day's* H/L/C marks the session's
   expected value area; price breaking above the top central level signals a
   trend-up day. A narrow prior-day CPR is the classic higher-conviction
   trend-day tell (used for confidence scoring here, not as an entry gate).
   One of the most widely used intraday frameworks among Indian traders.
3. **Entry conditions:** after the 15-minute opening range, the bar's **close
   crosses above the prior session's CPR top** (`TC/BC` math: `P=(H+L+C)/3`,
   `BC=(H+L)/2`, `TC=2P−BC`; top = max(TC,BC)), AND volume ≥ **1.5× its 20-bar
   average**, AND a prior-session CPR exists (no first-day trades).
4. **Exit conditions (owned):** its published CPR-trade form — first target at
   the prior-day floor-pivot R1, where **half the position is booked and the
   stop moves to breakeven**; the remainder runs to R2, the stop, or the
   session square-off. Never overnight.
5. **Stop-loss rules (owned):** below the CPR bottom (structural).
6. **Trailing stop rules (owned):** none.
7. **Timeframe:** 15m bars; CPR levels from the completed prior session (causal,
   prior-session-shifted).
8. **Intended market conditions:** trend-up days out of a value area (bull,
   range). Fails when the prior-day CPR is wide (break is deep inside range),
   on gap-up opens already above the CPR (no clean break bar), and midday fades.
9. **Markets/stocks commonly used:** extremely popular on NSE (equities and
   index futures) among Indian discretionary intraday traders. Implemented
   universe: 99 NIFTY-100 equities.
10. **Current implementation status:** implemented new in Phase 15 (production
    baseline #4) with a new causal `central_pivot_range` indicator; backtested
    (Phases 15/17). Evidence status `rejected`; not paper-traded.
11. **Files:** `src/algo/strategies/library/cpr_breakout_15m.py`;
    `central_pivot_range` in `src/algo/core/indicators.py` (CPR formula +
    prior-session causality tests); spec `research/INTRADAY_PRODUCTION_BATCH1.md`
    §4; reports as §2.1.
12. **Complete or incomplete:** **complete** under its owned execution declaration.

### 2.5 First Pullback After Breakout — `first_pullback_15m`

1. **Strategy name:** First Pullback After Breakout (a.k.a. first pullback /
   breakout-retest continuation).
2. **Theory:** after an intraday breakout of the opening range, the *first*
   pullback that holds above the breakout level (a higher low) confirms demand;
   the resume above that pullback's high is a defined-risk continuation entry.
   This is also the library's closest implementation of the "ORB retest" idea
   (it requires the pullback to hold the OR level, though entry triggers on the
   resume, not on a literal retest touch).
3. **Entry conditions:** (a) this session's close crossed above the 15-minute
   opening-range high (breakout); (b) afterwards, the **first** down bar whose
   low stayed ≥ the OR high (holding pullback); (c) the current bar's **close
   crosses above that pullback bar's high** (resume) while still above the OR
   high. **One entry per session** (only the first resume after the first
   pullback).
4. **Exit conditions (owned):** 2R profit target; otherwise the stop;
   otherwise square-off at the session's last bar. Never overnight.
5. **Stop-loss rules (owned):** below the first pullback's low (the published
   defined-risk stop).
6. **Trailing stop rules (owned):** none.
7. **Timeframe:** 15m bars.
8. **Intended market conditions:** trend days with an orderly first dip (bull,
   range). Fails when the first pullback breaks the level (failed breakout),
   on no-pullback open-drives, and late-session resumes.
9. **Markets/stocks commonly used:** standard momentum-continuation entry
   taught across intraday equities/futures trading; well-suited to liquid NSE
   large-caps. Implemented universe: 99 NIFTY-100 equities.
10. **Current implementation status:** implemented new in Phase 15 (production
    baseline #5); backtested (Phases 15/17) — best-ranked of the five on nearly
    every axis in the Phase-15 comparison. Evidence status `rejected`; not
    paper-traded.
11. **Files:** `src/algo/strategies/library/first_pullback_15m.py`; spec
    `research/INTRADAY_PRODUCTION_BATCH1.md` §5; reports as §2.1.
12. **Complete or incomplete:** **complete** under its owned execution declaration.

### 2.6 EMA Pullback Continuation — `pullback_15m`

1. **Strategy name:** 15-minute EMA Pullback Continuation (the library's
   "EMA Pullback" strategy).
2. **Theory:** in an established intraday uptrend (fast EMA above slow EMA,
   price above the slow EMA), a shallow pullback to the fast EMA that
   immediately resumes tends to continue — buyers defending the trend's own
   moving average. Entry is the reclaim bar, never the falling knife.
3. **Entry conditions:** EMA20 > EMA50 AND close > EMA50 (uptrend), AND within
   the last 5 bars some bar's low touched/undercut EMA20 (the dip), AND the
   current bar's **close crosses back above EMA20** (the reclaim). RSI(14) and
   volume feed the confidence score, not the entry gate.
4. **Exit conditions (owned):** stop → trailing ratchet → square-off at the
   session's last bar. No target (none is recorded for this strategy anywhere
   in the repository; it declares its pre-reset profile as its own). Never
   overnight.
5. **Stop-loss rules (owned):** wider of 2×ATR(14) and the 10-bar swing-low
   distance, capped at 6%.
6. **Trailing stop rules (owned):** ATR chandelier (2×ATR, arms at +0.6%) +
   the monotonic profit-lock ladder.
7. **Timeframe:** 15m bars. Note: EMAs run over the full 15m series (they carry
   across sessions; no daily reset) — standard practice for intraday EMAs.
8. **Intended market conditions:** trending sessions (bull). Fails at trend
   exhaustion, in midday EMA-whipsaw chop, and against a bearish index tape.
9. **Markets/stocks commonly used:** the generic "buy the pullback in a trend"
   intraday template used across markets; applied here to 99 NIFTY-100
   equities.
10. **Current implementation status:** implemented (Phase 4); measured on the
    full store in Phase 7 (D-026) — the highest-turnover 15m candidate of that
    batch (~62k signals). Evidence status `rejected`; not part of the Phase-15
    "production five" (its cousin `first_pullback_15m` supersedes it there);
    not paper-traded.
11. **Files:** `src/algo/strategies/library/pullback_15m.py`.
12. **Complete or incomplete:** **complete** under its owned execution declaration.

### 2.7 Volatility Expansion Breakout — `volexp_1h`

1. **Strategy name:** 1-hour Volatility Expansion Breakout (Bollinger squeeze
   breakout — the library's intraday squeeze strategy).
2. **Theory:** volatility is cyclical; when 1h Bollinger bandwidth compresses
   for several bars (squeeze), the eventual break tends to travel — energy
   stored during compression is released directionally.
3. **Entry conditions:** the **prior** 3 bars' Bollinger(20, 2σ) bandwidth all
   ≤ **0.03** (squeeze measured on prior bars only, so the breakout bar's own
   expansion can't disqualify it), AND the current bar's **close crosses above
   the upper Bollinger band**.
4. **Exit conditions (owned):** stop → trailing ratchet → square-off at the
   session's last bar. No target (none is recorded for this strategy anywhere
   in the repository; it declares its pre-reset profile as its own). Never
   overnight.
5. **Stop-loss rules (owned):** wider of 2×ATR(14) and the 10-bar swing-low
   distance, capped at 6%.
6. **Trailing stop rules (owned):** ATR chandelier (2×ATR, arms at +0.6%) +
   the monotonic profit-lock ladder.
7. **Timeframe:** 1h bars.
8. **Intended market conditions:** post-compression expansion (range → bull).
   Fails on false breaks back into the band, news-gap "breakouts", and thin
   names where the squeeze is illiquidity.
9. **Markets/stocks commonly used:** Bollinger/TTM-squeeze breakouts are used
   across equities and futures; applied here to 99 NIFTY-100 equities.
10. **Current implementation status:** implemented (Phase 4); measured Phase 7
    (D-026). Evidence status `rejected`; not paper-traded. (A *weekly/daily*
    squeeze variant for swing trading exists separately: `squeeze_daily`, §3.)
11. **Files:** `src/algo/strategies/library/volexp_1h.py`.
12. **Complete or incomplete:** **complete** under its owned execution declaration.

### 2.8 Gap-and-Go — `gapgo_15m` *(added 2026-07-19)*

1. **Name:** Gap-and-Go (overnight-gap momentum continuation).
2. **Theory:** a ≥2% overnight up-gap marks a demand imbalance; when the
   session's first bar holds the gap and its high then breaks in the opening
   phase, the imbalance continues (momentum day-trading canon —
   Warrior/SMB-style curricula; Indian opening-session practice).
3. **Entry:** gap ≥ +2% vs prior close AND first bar held (low > prior close,
   close ≥ open) AND a bar within the first 6 post-open bars closes above the
   first bar's high.
4–6. **Exits (owned):** stop below the first bar's low; 2R target; square-off.
7. **Timeframe:** 15m. 8. **Conditions:** gap mornings only; fails on
   exhaustion gaps and gap-and-fade days. 9. **Instruments:** liquid gappers /
   NSE F&O names. 10–12. **Status:** implemented + unit-tested (batch 2);
   **not yet backtested**; `library/gapgo_15m.py`; complete.

### 2.9 Inside-Bar Breakout — `insidebar_15m` *(added 2026-07-19)*

1. **Name:** Inside-Bar Breakout. 2. **Theory:** the two-bar equilibrium
   (inside bar within its mother bar) resolves through the mother's extreme
   and follows through (Crabel ID/NR lineage; Brooks; price-action canon).
3. **Entry:** same-session mother+inside pair; a close above the mother high
   within 2 bars of the pattern. 4–6. **Exits (owned):** stop below the
   inside bar's low; 2R target; square-off. 7. 15m. 8. Fails in midday chop
   and wide-mother patterns. 9. Any liquid intraday name.
10–12. Implemented + tested; **not yet backtested**;
   `library/insidebar_15m.py`; complete.

### 2.10 Supertrend Continuation — `supertrend_15m` *(added 2026-07-19)*

1. **Name:** Supertrend(10, 3) Continuation. 2. **Theory:** the
   volatility-adjusted trailing band flips state when price closes through
   it; being long with the bullish state rides trend legs (Seban; the most
   widely used intraday indicator in Indian retail practice).
3. **Entry:** the bar whose close flips the state bullish. 4–6. **Exits
   (owned):** initial stop at the line; **trail the line itself** (the
   engine's `column` trail — exits intrabar exactly where the state would
   flip); no target; square-off. 7. 15m (Wilder-ATR formulation, new
   `supertrend` indicator in `core/indicators`). 8. Whipsaws in ranges —
   known and accepted in the canon. 9. NSE F&O names/indices.
10–12. Implemented + reference-tested; **not yet backtested**;
   `library/supertrend_15m.py`; complete.

### 2.11 CPR Reversal — `cpr_reversal_15m` *(added 2026-07-19)*

1. **Name:** CPR Reversal (rotational-day S1 rejection) — the library's
   first intraday MEAN-REVERSION strategy. 2. **Theory:** a WIDE prior-day
   CPR forecasts rotation, not trend (Ochoa's day-typing — the mirror of the
   breakout's narrow-day tell); the S1 floor-pivot rejection is responsive
   buying at the range's edge, targeting rotation back to value.
3. **Entry:** prior-day CPR width > trailing 20-session median AND the bar
   tags S1 (low ≤ S1) while closing back above it (first rejection of the
   excursion, or the delayed reclaim cross). 4–6. **Exits (owned):** stop
   below the rejection bar's low; **target the central floor pivot**;
   square-off. 7. 15m (new `floor_pivot_supports` indicator).
8. Wide-CPR rotational days; fails on genuine trend-down days.
9. NSE F&O names — the Indian pivot-trading staple. 10–12. Implemented +
   gate-tested; **not yet backtested**; `library/cpr_reversal_15m.py`;
   complete.

### 2.12 NR7 Intraday — `nr7_intraday_15m` *(added 2026-07-19)*

1. **Name:** NR7 Intraday (Crabel range-expansion day-trade). 2. **Theory:**
   the narrowest daily range of seven marks multi-day compression; breaking
   that day's high the NEXT session captures the expansion day itself
   (Crabel — the original narrow-range research; closes the "NR7 Intraday"
   gap in the target list; distinct from the swing `nr7_daily`).
3. **Entry:** yesterday was NR7 AND a bar closes above yesterday's high.
   Price-only, parameter-light, per Crabel. 4–6. **Exits (owned):** stop
   below the NR7 day's low (the compression range is the risk); no target
   (ride the expansion day); square-off. 7. 15m (new `prior_session_ohlc`
   helper). 8. Fails on downward resolutions and big gap-overs.
9. Liquid NSE names. 10–12. Implemented + gate-tested; **not yet
   backtested**; `library/nr7_intraday_15m.py`; complete.

---

## 3. Daily swing strategies (23) — implemented, but NOT intraday

These hold for days to months (`Product.DELIVERY`, ~30.9 bps round-trip cost)
and therefore **do not meet the same-day entry/exit requirement** of the reset
objective. They are documented for completeness and remain in the repository
untouched. Shared facts (all 23): timeframe **1d**; each declares its own
swing execution (`atr_trail_swing`: ATR/structure stop, chandelier trail,
overnight allowed, `horizon_end` at its `max_hold_bars`); long-only;
implemented as complete, tested entry-signal modules; all carry evidence status
`rejected` under the frozen research gate (none cleared the
selection-edge-over-cost bar — that is a *research verdict*, not an
implementation defect); none is paper-traded or live.

### 3.1 Per-symbol daily strategies (batch 1 + phase 4)

| Strategy | File (`…/library/`) | Theory (one line) | Entry conditions (exact) | Max hold |
|---|---|---|---|---|
| `ema200_daily` — 200-EMA Pullback Trend | `ema200_daily.py` | Deep pullbacks to a rising 200-day EMA are defended by longer-horizon buyers | Close > EMA200 & EMA200 rising (vs 20 bars ago) & a low within +3% of EMA200 in the last 10 bars & close crosses back above EMA20 | 8 d |
| `nr7_daily` — NR7 Volatility Contraction | `nr7_daily.py` | Narrowest range of 7 days marks coiling; next-day break of that high starts expansion (Crabel) | Yesterday was NR7 & today's close crosses above yesterday's high & volume ≥ 1.2× 20-day avg | 8 d |
| `tsmom_daily` — Time-Series Momentum | `tsmom_daily.py` | A stock's own 12-month return (skip most recent month) persists 1–3 months (Moskowitz et al.) | 12-1 momentum (`close[t−21]/close[t−252]−1`) crosses above 0 | 60 d |
| `hi52_daily` — 52-Week-High Proximity | `hi52_daily.py` | Anchoring on the 52-week high delays good news; entering the near-high band overcomes the anchor (George-Hwang) | `close / prior-252d-high` crosses above 0.95 | 60 d |
| `egap_daily` — Earnings-Gap Continuation (PEAD proxy) | `egap_daily.py` | Prices under-react to earnings-type events; a big held gap drifts further for weeks | Overnight gap ≥ +3% & volume ≥ 3× 20-day avg & close ≥ open (gap held) | 40 d |
| `donchian55_daily` — Donchian 55-Day Breakout | `donchian55_daily.py` | A close above the prior 55-day high clears a quarter's sellers; big trends start at new highs (Turtles) | Close crosses above the prior 55-day high | 60 d |
| `squeeze_daily` — Weekly Volatility Squeeze | `squeeze_daily.py` | Weekly BB inside Keltner = abnormal calm; daily upside range-break resolves the stored move (TTM squeeze) | Weekly BB(20,2σ) inside Keltner(20, 1.5×ATR) (as-of completed weeks) & daily close crosses above prior 10-day high | 30 d |
| `hvol_daily` — High-Volume Return Premium | `hvol_daily.py` | An abnormal-volume day is an attention shock that lifts price for weeks (Gervais et al.) | 50-day volume ratio crosses above 3.0 | 20 d |
| `triple_screen_daily` — Elder Triple Screen | `triple_screen_daily.py` | Weekly tide filters daily wave: buy the end of a daily pullback inside a rising weekly trend | Weekly EMA13 rising (as-of) & yesterday's 2-day Force Index < 0 & close crosses above yesterday's high | 20 d |
| `wyckoff_spring_daily` — Wyckoff Spring | `wyckoff_spring_daily.py` | A failed break below a 30-day range low traps sellers; their covering fuels the markup | Low breaks the prior 30-day range low & close back above it (same bar, or crossing back within 3 bars) | 40 d |
| `tom_daily` — Turn-of-Month | `tom_daily.py` | Returns concentrate at month boundaries (India: dated SIP inflows) | First bar within 2 business days of month-end (pure calendar window) | 7 d |
| `stage2_daily` — Weinstein Stage 2 | `stage2_daily.py` | Stage 1→2 transition: base breakout above a rising 30-week MA on volume marks the markup phase | Close crosses above prior 150-day high & 150-day MA rising (vs 20 bars ago) & close > MA & volume ≥ 1.5× 50-day avg | 126 d |
| `breadth_regime_daily` — Breadth-Regime Momentum | `breadth_regime_daily.py` | Breakouts work when participation is broad; gate on market breadth | >50% of universe above its 200-day MA & close crosses above prior 50-day high | 63 d |

*(Fields 8–9 for this group: intended conditions are per-strategy regimes
declared in each module's `meta` — mostly bull/range; commonly used on liquid
equities generally; implemented universe NIFTY-500 daily. Fields 10–12: complete;
measured on the NIFTY-500 corpus in Phases 9–11/14; all `rejected` under the
frozen gate.)*

### 3.2 Cross-sectional daily strategies (batch 2)

These rank the whole universe each day (`prepare_cross_section` seam) and enter
a stock when it joins the favoured decile. Entry trigger for all of them: the
stock **enters** the favoured decile (edge-triggered on the 0→1 transition of
the decile flag; deciles need ≥10 ranked names on the date).

| Strategy | File | Ranked metric | Favoured tail | Max hold |
|---|---|---|---|---|
| `xsmom_daily` — Cross-Sectional Momentum | `xsmom_daily.py` | 12-1 return (`close[t−21]/close[t−252]−1`) | top decile | 63 d |
| `resmom_daily` — Residual Momentum | `resmom_daily.py` | cumulative market-residual return over [t−126, t−21] (rolling-beta vs equal-weight universe) | top decile | 63 d |
| `dualmom_daily` — Dual Momentum | `dualmom_daily.py` | 12-1 return, **AND** own 12-1 > 0 (absolute gate) | top decile | 63 d |
| `lowvol_daily` — Low Volatility | `lowvol_daily.py` | 126-day daily-return σ | bottom decile | 126 d |
| `bab_daily` — Betting Against Beta | `bab_daily.py` | 252-day rolling beta vs equal-weight universe | bottom decile | 126 d |
| `xsrev_daily` — Short-Term Reversal | `xsrev_daily.py` | 5-day return | bottom decile (losers) | 21 d |
| `maxret_daily` — Anti-Lottery (low MAX) | `maxret_daily.py` | max single-day return over 21 days | bottom decile | 63 d |
| `hi52rank_daily` — 52-Week-High Rank | `hi52rank_daily.py` | `close / 252-day high` | top decile | 126 d |
| `illiq_daily` — Amihud Illiquidity | `illiq_daily.py` | mean(|ret| / traded value) over 63 days | top decile (most illiquid) | 126 d |
| `combo_lowvol_mom_daily` — Low-Vol × Momentum Composite | `combo_lowvol_mom_daily.py` | mean percentile of (12-1 momentum, inverse 126-day vol) | top decile | 126 d |

*(Same shared facts as §3.1: theory per module docstrings — Jegadeesh-Titman,
Blitz-Huij-Martens, Antonacci, Ang et al., Frazzini-Pedersen, Bali et al.,
George-Hwang, Amihud; complete; measured Phase 11 on NIFTY-500; all `rejected`
by the frozen gate at long horizons; not intraday.)*

---

## 4. Research artifacts that are NOT tradeable strategies

For completeness — these exist in the repository but are measurement tools or
archived research declarations, not entries in the strategy library:

- **R-001 hypothesis objects** (`scripts/research_r001.py`, framework in
  `research/hypothesis.py` / `components.py`): three archived short-horizon
  research hypotheses (F&O-expiry week, gap-fade, month-start), declared
  `enabled=False`, measured and archived in Phase 14. Not registered strategies.
- **Fidelity evaluation engine** (`src/algo/research/fidelity.py`,
  `scripts/fidelity_eval.py`, `tests/test_fidelity.py` — **uncommitted**,
  Phase 17): an isolated research execution engine that emulates *published*
  execution (trigger-level entries, structural stops such as OR-low / below-VWAP
  / CPR-bottom / pullback-low, 1×-range and floor-pivot targets, partial +
  breakeven, honest gap-through fills). Used to audit the five production
  intraday baselines against their published forms; zero production imports.
- **Benchmark battery** (`src/algo/research/benchmarks.py`): random-entry and
  buy-and-hold baselines used by the research gate.

---

## 5. Coverage vs the ~20 target intraday strategies

Status columns reflect the **intraday** form of each target. "Backtested?"
means an intraday backtest exists on the 15m/1h store (Phase 7 / Phase 15 /
the new ₹50k standardized run). Paper-tested: the paper engine has never run a
session (it is gated on research verdicts and none passed). Ready for Live: no
live order path exists in the repository at all (paper simulation only), so
**No** for every strategy.

| # | Target strategy | Implemented? | Backtested? | Paper-tested? | Ready for Live? | Missing components |
|---|---|---|---|---|---|---|
| 1 | Opening Range Breakout (ORB) | **Yes** (`orb_15m`) | Yes | No | No | — (baseline complete) |
| 2 | VWAP Trend | **Yes** (`vwap_15m`) | Yes | No | No | — |
| 3 | VWAP Pullback | **Yes** (`vwap_pullback_15m`) | Yes | No | No | — (encoding over-fires vs discretionary use; selectivity filter absent) |
| 4 | CPR Breakout | **Yes** (`cpr_breakout_15m`) | Yes | No | No | — |
| 5 | CPR Reversal | **Yes** (`cpr_reversal_15m`, added 2026-07-19) | Not yet | No | No | — |
| 6 | Opening Drive | **No** | No | No | No | Strategy module (strong directional move from the open, e.g. first-bar marubozu/momentum) |
| 7 | Gap and Go | **Yes** (`gapgo_15m`, added 2026-07-19) | Not yet | No | No | — |
| 8 | First Pullback | **Yes** (`first_pullback_15m`) | Yes | No | No | — |
| 9 | Initial Balance Breakout | **Partial** | No (as IB) | No | No | `orb_15m` with `range_minutes=60` IS an IB breakout, but no 60-min variant is registered/backtested; needs a registered config |
| 10 | NR7 Intraday | **Yes** (`nr7_intraday_15m`, added 2026-07-19) | Not yet | No | No | — (`nr7_daily` remains the swing form) |
| 11 | Inside Bar Breakout | **Yes** (`insidebar_15m`, added 2026-07-19) | Not yet | No | No | — |
| 12 | Volume Breakout | **No** | No | No | No | Strategy module (price break of recent high on volume surge, intraday). `volume_ratio` + breakout helpers exist; `hvol_daily` is a different (swing, attention-shock) mechanism |
| 13 | ORB Retest | **Partial** | Via §2.5 | No | No | `first_pullback_15m` covers hold-above-OR + resume; a literal retest-touch entry at the OR high is not implemented |
| 14 | Trendline Break | **No** | No | No | No | Swing-point detection + trendline fitting (nothing exists); strategy module |
| 15 | EMA Pullback | **Yes** (`pullback_15m`) | Yes | No | No | — |
| 16 | Supertrend Continuation | **Yes** (`supertrend_15m`, added 2026-07-19) | Not yet | No | No | — (`supertrend` indicator now in `core/indicators`) |
| 17 | Bollinger Squeeze Breakout | **Yes** (`volexp_1h`, 1h) | Yes | No | No | 15m variant unregistered (1h version implemented; `squeeze_daily` is the swing cousin) |
| 18 | Donchian Breakout | **Partial** | Daily form only | No | No | `donchian55_daily` is positional (55-day, delivery). Intraday version: shorter channel on 15m + MIS square-off |
| 19 | Momentum Continuation | **No** | No | No | No | Dedicated intraday momentum-continuation module (e.g. N-bar thrust + continuation). Daily `tsmom`/`xsmom` are positional factor strategies, not this |
| 20 | Relative Strength Breakout | **No** | No | No | No | Intraday RS-vs-index computation (needs index intraday data alongside stocks) + module. Daily cross-sectional RS (`xsmom`, `resmom`) is positional |

**Coverage count: 12 Yes · 3 Partial · 5 No** against the 20 targets (updated 2026-07-19 with batch 2).
Everything marked Yes is backtested in the standardized ₹50,000 report
(`user_data/backtest_results/reports/intraday_50k_backtest.md`).
