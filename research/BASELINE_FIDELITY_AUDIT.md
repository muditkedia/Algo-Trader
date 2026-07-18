# Baseline Fidelity Audit — Intraday Production Batch 1

_Phase 16, 2026-07-18. Question under audit: **"Are we testing the published
strategies, or modified versions created by our common execution framework?"**
No code was modified; the execution semantics cited below are PINNED by
characterization tests (`tests/test_execution_audit.py`) — verified facts, not
readings of the code._

**Verdict up front: we are testing the published ENTRY IDEAS under a
substantially DIFFERENT execution model.** Four framework-wide deviations are
material and shared by all five strategies: (1) long-only, (2) entry at the
signal candle's CLOSE instead of at the trigger level, (3) no profit targets,
(4) ATR/structure stops instead of the published tight structure stops. Each is
an intentional, documented platform policy (D-006/D-023, evidence-backed from the
crypto phase) — but the published systems were not designed around them, so the
Phase-15 baseline measures "our platform trading these entries", not "these
strategies as published". Attribution (Part E) quantifies which differences bite.

---

## Part A — Strategy fidelity, line by line

Legend: ✔ exact · Δ intentional deviation (platform policy) · ◻ platform
constraint · ≈ simplification · ⚠ assumption introduced by our framework.

### A1. Opening Range Breakout (orb_15m)
- **Published form** (Crabel, *Day Trading with Short Term Price Patterns and
  Opening Range Breakout*, 1990; standard Indian F&O practice): define the range
  from the first 15/30 min; BUY STOP order at range-high (SELL STOP at range-low
  — both directions); stop-loss at the opposite range extreme or range midpoint;
  target 1–2× range or trail; trade liquid, volatile names; morning entries.
- **Our implementation vs published:**

| Element | Published | Ours | Class |
|---|---|---|---|
| Range window | first 15–30 min | first 15 min | ✔ |
| Direction | long AND short | long only | ◻ (platform long-only) |
| Entry execution | stop order AT range high (intrabar touch) | bar CLOSE after a close above the high | ⚠ **material** — pays the full breakout-bar extension (measured +34 bps mean chase) |
| Volume condition | optional/contextual | MANDATORY ≥1.5× | Δ (documented in Phase-4 spec) |
| Stop | OR low / midpoint (tight, structural) | wider of 2×ATR & swing-low, ≤6% (mean ~1.7%) | Δ (D-023 uniform risk engine) |
| Target | 1–2× range | none — trail/square-off only | Δ (D-006: no targets) |
| Square-off | 15:15–15:20 MIS | session's last bar | ✔/≈ |

### A2. VWAP Pullback (vwap_pullback_15m)
- **Published form** (institutional VWAP-execution lore; Brian Shannon's AVWAP
  practice): in an up-trending session with price above a RISING VWAP, buy the
  pullback that tags VWAP and holds; stop just below VWAP; target prior high /
  R-multiple; skip flat-VWAP days.
- **Deviations:** entry at resume-bar close (⚠, not at the VWAP tag);
  stop = ATR/swing not "just below VWAP" (Δ — materially WIDER); no
  prior-high target (Δ); long-only ◻ (matches the long side of the published
  form); tag-band 0.2% and resume=prior-bar-high are OUR encodings of a
  discretionary description (≈ — the published rule is not fully mechanical, so
  SOME encoding was unavoidable; ours proved far too permissive: 115k trades).

### A3. VWAP Trend Continuation (vwap_15m)
- **Published form**: on a buyer-controlled day, buy the reclaim after a dip
  through VWAP; stop below the dip low; target measured vs the day's range.
- **Deviations:** same four framework deltas (close entry ⚠, ATR stop Δ, no
  target Δ, long-only ✔-compatible); the 60% prior-bars-above-VWAP control
  filter is our mechanization of "buyer-controlled" (≈, reasonable).

### A4. CPR Breakout (cpr_breakout_15m)
- **Published form** (Frank Ochoa, *Secrets of a Pivot Boss*, 2010; ubiquitous in
  Indian intraday practice): CPR = pivot(H+L+C)/3, BC=(H+L)/2, TC=2P−BC from the
  PRIOR day; narrow CPR ⇒ trending day; buy the break above TC (often with a
  retest), stop below the pivot/BC, targets at R1/R2 floor-pivot levels; both
  directions.
- **Deviations:** CPR math ✔ (formula and prior-day causality pin-tested);
  entry at close of the breaking bar ⚠ (published: at/near the level, often on
  retest); stop ATR-based, not below pivot/BC (Δ); NO R1/R2 targets (Δ —
  Ochoa's system is explicitly target-driven, so this deviation cuts deepest
  here); narrow-CPR is confidence-only, not a gate (≈ — published treats narrow
  CPR as the setup filter); long-only ◻; volume gate added Δ.

### A5. First Pullback After Breakout (first_pullback_15m)
- **Published form** (classic momentum continuation; Landry's "first pullback",
  Elder/O'Neil variants intraday): after a breakout, the first orderly pullback
  that HOLDS the level; buy above the pullback bar's high; stop below the
  pullback low; target = measured move/prior high.
- **Deviations:** the sequence logic (breakout → first HOLDING pullback → resume,
  one per session) ✔ faithful and pin-tested; entry at resume-bar close ⚠
  (published: buy-stop above the pullback high — small chase here since the
  trigger is the pullback high itself); stop ATR/swing vs below-pullback-low (Δ
  — published stop is TIGHTER, right under structure); no measured-move target Δ;
  long-only ◻.

**Part A conclusion:** entry CONDITIONS are faithful (✔/≈) for all five; entry
EXECUTION, STOPS, TARGETS and DIRECTION are platform-substituted (⚠/Δ/◻) for all
five. The published risk/reward architecture (tight structural stop + defined
target) is replaced everywhere by wide-ATR-stop + open-ended trail.

---

## Part B — Execution model, pinned facts

All verified by `tests/test_execution_audit.py` (+ Phase-15's
`test_intraday_production.py`):

| Behaviour | Audited fact | Test |
|---|---|---|
| Entry timing | **signal candle CLOSE** (not next open, not the trigger level, not intrabar) | `test_entry_fills_at_signal_bar_close` |
| Entry bar exits | the entry bar itself can NEVER stop the trade; exits evaluated from the next bar | `test_entry_bar_cannot_stop_out_the_trade` |
| Stop execution | intrabar vs bar LOW, checked BEFORE any favourable excursion (conservative ordering); fill booked **AT the stop price even through a gap** — no gap-through slippage (**optimistic**) | `test_stop_fill_is_at_stop_price_even_through_a_gap` |
| Target execution | **no targets exist anywhere** | `test_no_profit_target_exists` |
| Trailing | chandelier 2×ATR arms only at ≥ +0.6% profit; monotonic ratchet (never widens); profit-lock tiers at +1.0/1.8/2.8/4.5% | `test_stop_only_tightens_and_trail_needs_activation_profit` |
| Square-off | intraday closes on the session's LAST BAR (incl. partial days) — not a hardcoded clock time | `test_partial_day_squares_off_on_its_last_bar`, Phase-15 test |
| VWAP reset | per calendar day (one NSE session/day) | Phase-15 `test_session_vwap_resets_each_session` |
| CPR calculation | prior-session H/L/C, shifted — NaN on day 1, constant within a session | Phase-15 `test_cpr_uses_prior_session_and_formula` |
| ORB calculation | bars STARTING within 15 min of the session's first bar; boundaries never bleed across sessions | Phase-15 ORB/boundary tests |
| Gap handling (entry) | a gap-up already above the level does NOT count as a cross (needs prior bar at/below) | Phase-15 `test_gap_up_open_does_not_spurious_break` |
| Missing candles / holidays | store is quality-gated; absent bars simply absent; sessions grouped by calendar day, so a missing day is a missing session — no interpolation | design (D-025) |

Two audit flags: (i) close-of-bar entry is systematically ADVERSE vs the
published stop-order execution (quantified in Part E); (ii) at-stop gap fills are
systematically OPTIMISTIC (mild intraday; would matter for delivery).

---

## Part C — Cost model vs current Angel One intraday charges

Model (`NseCostParams`) vs Angel One's published equity-intraday schedule:

| Component | Our model | Angel One current | Verdict |
|---|---|---|---|
| Brokerage | min(0.03% × turnover, ₹20)/order | ₹20 or 0.1%, whichever lower | ✔ at our ₹1L stake both bind at ₹20; our 0.03% understates only for orders < ~₹20k (not used) |
| STT | 0.025% SELL side only | 0.025% sell side | ✔ exact |
| Exchange txn (NSE) | 0.00297% | **0.0030699%** | ⚠ marginally outdated (post-Oct-2024 slab); understates ~0.026 bps/round-trip (~0.2% of total cost) |
| SEBI fee | ₹10/crore (0.0001%) | ₹10/crore | ✔ |
| GST | 18% on brokerage+txn+SEBI | 18% on brokerage+txn | ✔ (incl. SEBI is standard) |
| Stamp duty | 0.003% BUY side | 0.003% buy side | ✔ |
| Slippage | 2 bps/side (assumption) | n/a (not a charge) | reasonable-to-conservative for NIFTY-100 names at ₹1L clip with marketable orders; NOT conservative for illiquid names or larger size |

**Conclusion:** the cost stack is essentially current and, if anything, ~0.03 bps
per round trip OPTIMISTIC (outdated NSE slab). Cost assumptions are NOT the
source of the losses' magnitude — per instruction, nothing changed yet.
Sources: [Angel One exchange & transaction charges](https://www.angelone.in/exchange-transaction-charges),
[Chittorgarh: Angel One brokerage 2026](https://www.chittorgarh.com/brokerage_charges/angel-broking/14/).

---

## Part D — Universe suitability (analytical only; no filters introduced)

Current universe: 99 NIFTY-100 names (the only 15m data held), all traded
equally.

| Strategy | Typical published deployment | Current universe fit | Assessment |
|---|---|---|---|
| ORB | high-beta, news/gap-active, liquid F&O names; often pre-filtered by gap% or prior-day NR | NIFTY-100: liquid ✔ but includes many low-beta defensives where the OR is noise | acceptable; published practice implies a volatility/gap pre-filter we do not apply |
| VWAP Pullback | large-cap institutional names on TRENDING days | universe ✔; the missing piece is the trending-DAY condition, not the symbol list | universe fine; day-type is the gap |
| VWAP Trend Cont. | same | same | same |
| CPR Breakout | F&O liquid names; explicitly narrow-CPR days | universe ✔; published narrow-CPR day filter is confidence-only in ours | universe fine; day-type filter is the deviation (recorded in A4) |
| First Pullback | momentum/trend names | universe ✔ | fine |
| Liquidity/volume thresholds | implied everywhere (spread ≪ cost) | NIFTY-100 satisfies this by construction | ✔ no threshold needed at this universe; WOULD be needed before any NIFTY-500 intraday extension |

**Conclusion:** the universe is consistent with typical deployment for all five
(liquid large caps). The gap is not WHICH symbols but WHICH DAYS — published
practice conditions several of these setups on day-type (gap, narrow-CPR,
trend day) that we deliberately did not encode as gates. No filter introduced.

---

## Part E — Attribution analysis (measured, 40-symbol subset)

Decomposition from the simulator's own trade records (gross vs net; P&L by exit
reason; mean entry-chase above the published trigger level):

| strategy | n | gross exp. | cost | net | **entry-chase** | stop dist. | square-off bucket | stop bucket | trail bucket |
|---|---|---|---|---|---|---|---|---|---|
| orb | 9,874 | **+0.1** | 12.2 | −12.1 | **+33.9** | 1.67% | 86% @ −7.2 | 7.4% @ −121 (0% win) | 6.5% @ +45 |
| vwap_pullback | 46,511 | +0.6 | 12.2 | −11.6 | **+43.6** | 1.18% | 88% @ −4.2 | 9.2% @ −97 | 2.9% @ +37 |
| vwap | 10,770 | −0.1 | 12.2 | −12.3 | +14.4 | 1.01% | 85% @ −1.8 | 12.6% @ −93 | 2.7% @ +34 |
| cpr | 5,525 | −1.6 | 12.2 | −13.8 | **+29.0** | 1.22% | 83% @ −4.2 | 12.5% @ −106 | 5.0% @ +57 |
| first_pullback | 7,102 | **+3.1** | 12.2 | −9.2 | +19.2 | 1.63% | 91% @ −5.9 | 4.9% @ −110 | 3.7% @ +42 |

(bps per trade; buckets show share of trades @ mean net bps.)

**Ranked attribution of the underperformance:**

1. **Trading costs — the certain, exact component.** Gross expectancy is ~ZERO
   for all five (−1.6 to +3.1 bps); net = gross − 12.2. The entire net loss is
   the cost stack applied to zero-edge fills — L-006 reproduced yet again:
   gross-flat, fees are the killer.
2. **Entry execution timing — the largest RECOVERABLE candidate.** The
   close-of-bar fill pays a measured +14 to +44 bps "chase" above the published
   trigger level (upper bound on the recoverable amount, since trigger-level
   stop orders also fill on touches that fail). Even HALF the chase exceeds the
   whole cost stack for orb/vwap_pullback/cpr. This is a framework artifact, not
   a property of the published strategies.
3. **Exit methodology — reshapes the distribution; mean impact uncertain.**
   83–91% of trades never hit stop or trail and simply ride to square-off at a
   small mean loss (−2 to −7 bps): the modal trade is a costed noise round-trip.
   The published tight-stop + 1–2R-target architecture would produce a very
   different shape. But the crypto evidence (L-006, D-006) showed exit redesign
   does not create MEAN edge, so this ranks below entry timing for the mean —
   while explaining the "always slightly negative every month" pattern.
4. **Signal quality / day-type conditioning.** With chase removed, the implied
   at-level gross is at best a few tens of bps; published practice adds day-type
   gates (gap, narrow-CPR, trend day) we did not encode. vwap_pullback's 5–9×
   over-firing is OUR too-permissive encoding of a discretionary setup — a
   signal-definition issue, not a market verdict.
5. **Universe — minimal contribution** (Part D: appropriate for all five).

## Part F — Readiness assessment (per strategy)

Recommendation categories: 1 = faithful, baseline accepted · 2 = material
implementation differences require correction before further research · 3 =
insufficient evidence.

| Strategy | Verdict | Basis |
|---|---|---|
| orb_15m | **2** | Entry condition faithful; but stop-order→close-entry (+34 bps chase), long-only, no range target, ATR stop replace the published system's economics. |
| vwap_pullback_15m | **2 (with a 3 caveat)** | Framework deltas as above PLUS our mechanization of a discretionary setup over-fires 5–9×; the source is not fully objective, so complete fidelity is not determinable. Encoding must be tightened before results say anything about the published idea. |
| vwap_15m | **2 (closest to 1)** | The published entry IS a close-based reclaim (smallest chase, +14 bps); deviations are stop/target/direction only. |
| cpr_breakout_15m | **2** | CPR math exact (pin-tested), but Ochoa's system is explicitly TARGET-driven (R1/R2) with pivot-based stops — the deviation cuts deepest here; narrow-CPR is published as a gate, ours is confidence-only. |
| first_pullback_15m | **2 (lightest)** | Sequence logic faithful and pin-tested; smallest structural chase (trigger = pullback high); best gross (+3.1). Still lacks the published under-structure stop and measured-move target. |

**Overall: NO baseline is accepted as a faithful implementation of its published
system (zero category-1 verdicts).** The Phase-15 numbers are a valid baseline of
*these entries under our frozen execution model* — but they must NOT be read as
"the published strategies fail on NSE." The four framework substitutions
(long-only, close entry, no targets, ATR stops) are frozen, evidence-backed
platform policy, so the correction path is NOT to modify frozen engines but an
owner decision on a **fidelity evaluation mode** (trigger-level entry
approximation + published stop/target emulation, as a measurement variant) before
any selectivity filters or AI-guided refinement — otherwise the improvement phase
would optimise against an artifact (the chase) rather than the strategies.
