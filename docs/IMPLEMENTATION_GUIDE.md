# IMPLEMENTATION GUIDE — how practitioners actually trade the intraday library

_Written 2026-07-19. Phase 1+2 of the practitioner-faithfulness pass.
Documentation only — no strategy code was modified._

**Scope:** the seven implemented intraday strategies (`orb_5m`, `vwap_15m`,
`vwap_pullback_15m`, `cpr_breakout_15m`, `orb_retest_5m`, `pullback_15m`,
`volexp_1h`). For each: the canonical practitioner implementation — including
the context and quality rules experienced traders apply that articles and
summaries usually omit — and exactly which of those elements our current
implementation has or lacks.

**Sources.** Rules below are limited to what is *consistently* recommended
across the canonical practitioner literature and widely accepted practice:
Toby Crabel (*Day Trading with Short Term Price Patterns and Opening Range
Breakout*), Mark Fisher (*The Logical Trader*), Frank Ochoa (*Secrets of a
Pivot Boss* — the text Indian CPR practice descends from), Brian Shannon
(AlphaTrends; *Maximum Trading Gains with Anchored VWAP*), Linda Raschke
(*Street Smarts*; first-pullback practice), Dave Landry (pullback method),
Al Brooks (price-action EMA pullback practice), John Bollinger (*Bollinger on
Bollinger Bands*), John Carter (*Mastering the Trade*, TTM squeeze), Alexander
Elder (tide/wave filtering), plus standard Indian intraday practice on NSE
cash/F&O (CPR/ORB conventions, MIS square-off discipline). Where practitioners
disagree, the disagreement is noted rather than resolved.

**Cross-cutting practitioner rules (apply to nearly every setup below).**
Experienced intraday traders overlay these on *any* long setup; they are part
of the strategy as practised, not optimisations:

- **Relative volume (RVOL), measured against the same time of day.** "Volume
  1.5–2× normal" always means normal *for that bar of the session* (volume has
  a U-shape: heavy open/close, quiet lunch). A rolling-20-bar average — what we
  currently use — understates morning RVOL and overstates midday RVOL.
- **Market alignment.** Long entries only when the index tape agrees (NIFTY
  above/reclaiming its session VWAP, or index OR broken upward). "Trade with
  the market" is the single most repeated rule in the day-trading literature
  (Elder's tide, Fisher's macro filter, SMB/Shannon practice).
- **Time-of-day windows.** Best breakout entries: ~09:30–11:30 IST. Lunch
  (~12:00–13:30) is avoided for fresh breakouts; entries after ~14:30 lack
  runway before the 15:15 square-off. (Our engine already forbids last-bar
  entries; practitioners cut off much earlier.)
- **Gap context.** The overnight gap changes the playbook: large gaps
  (≳1.5–2%) put the open far from value (breakout levels lose meaning or turn
  into gap-and-go/gap-fade setups); modest gaps in trend direction help.
- **Liquidity.** Liquid F&O-grade names only (our NIFTY-100 universe already
  satisfies this).
- **Event avoidance.** No fresh positions on a symbol's results day or on
  major macro-event days (RBI policy, budget, election results). Weekly index
  expiry days distort index-heavy names.
- **Setup-quality go/no-go.** Practitioners reject C-grade setups outright;
  our `confidence()` computes similar quality components but never gates.

---

## 1. Opening Range Breakout — `orb_5m` (current canonical implementation)

STRAT-01 now implements the master 5-minute, bidirectional design. It uses a
0.1% buffered break, same-slot 10-session RVOL (2.0 long / 2.5 short), NIFTY
and stock structure, 0.5–2.5 ATR opening-range width, regime/confidence gates,
a limit collar with confirmed-fill state transition, midpoint/1.5 ATR-capped
stop, 1.5R half exit, breakeven, post-partial 2 ATR chandelier, VWAP
invalidation, and a six-bar no-progress exit. See `docs/STRAT01_ORB_5M.md` for
the full contract and documented input substitutions.

### Retired `orb_15m` practitioner comparison (legacy evidence only)

1. **Original strategy name:** Opening Range Breakout (ORB).
2. **Canonical description:** the first minutes of the session establish the
   day's initial auction range; a break of that range on participation signals
   directional conviction for the session (Crabel originated the systematic
   form; Fisher's ACD is the institutional refinement; the 15-minute OR is the
   common Indian convention, with 5-minute and 30-minute variants widespread).
3. **Commonly accepted entry rules:** buy-stop at/just above the OR high (many
   retail practitioners wait for a close beyond the level to reduce false
   triggers — both conventions are legitimate; close-confirmation trades later
   but more reliably); breakout bar on elevated RVOL (≥ ~1.5× same-time-of-day
   norm); market/index breaking the same way; prefer a NARROW OR relative to
   recent daily range (compression precedes expansion — Crabel's core claim);
   morning breakouts preferred (first test of the level, before ~11:30).
4. **Commonly accepted exit rules:** first scale at 1× the OR height (the
   measured move — our implemented target) or at a fixed R multiple; runners
   held to session close ONLY on trend days (one-directional tape); otherwise
   flatten into strength or by early afternoon; hard square-off before the
   broker's 15:15 cutoff.
5. **Commonly accepted stop-loss rules:** below the OR low (full-risk
   version), or below the OR midpoint (half-range stop — Fisher's pivot
   range logic; tighter, used when the OR is wide); never wider than the OR.
6. **Commonly accepted trailing methods:** after 1R, stop to breakeven; then
   trail under successive 15m higher lows or under VWAP for runners. (No
   ATR-chandelier convention exists in the ORB literature.)
7. **Market conditions where experienced traders use it:** expected trend/
   expansion days — gap-in-direction mornings, narrow prior-day range (NR7),
   narrow OR, strong overnight/global cues, high pre-open interest.
8. **Market conditions where they deliberately avoid it:** wide/sloppy OR
   (already ≳ a normal day's range — nothing left to capture); inside/quiet
   opens with no participation; big exhaustion gaps (>~2%) against overnight
   value; lunch-hour breaks; major event days where the level flips repeatedly.
9. **Common confirmation filters:** RVOL ≥ 1.5–2×; index OR breaking the same
   direction; narrow OR vs ATR; breakout bar closing near its high; prior-day
   trend agreeing.
10. **Common rejection filters:** OR width > ~⅓–½ of the 14-day ATR (varies by
    teacher, universally *some* width cap); breakout after 3+ failed tests of
    the level; entry after ~14:00; symbol results day.
11. **Typical timeframe:** 5m/15m/30m bars; 15m OR is the NSE convention we
    implement.
12. **Typical holding period:** 30 minutes to 3–4 hours; always flat by close.
13. **Typical instruments:** liquid NSE F&O large-caps and index futures;
    (US practice: liquid gappers/megacaps).
14. **Common mistakes beginners make:** chasing an extended breakout 2–3 bars
    late; trading every symbol's OR without RVOL or market context; taking
    breaks on wide-OR days; counting the 09:07 pre-open auction print into the
    range; moving the stop; revenge-trading the re-break after a stop-out.
15. **Already implemented:** 15m OR (correct window, pre-open excluded);
    close-confirmed break entry; mandatory volume confirmation (rolling-20
    proxy); OR-low structural stop; 1×-range target; session square-off; no
    overnight; OR-tightness and close-location scored in `confidence()`.
16. **Important elements currently missing:** RVOL measured vs same
    time-of-day; index-alignment gate; OR-width **rejection** gate (currently
    only scored, never gates); gap-size rules; entry time-window cutoff
    (currently any bar after the OR qualifies, however late); event-day
    avoidance; breakeven-after-1R management; (optional variants: 5m OR,
    60m initial balance, retest entry).

## 2. VWAP Trend Continuation — `vwap_15m`

1. **Original strategy name:** VWAP reclaim / trend continuation ("VWAP
   Trend").
2. **Canonical description:** VWAP is the institutional fair-price benchmark;
   on buyer-controlled sessions, dips through VWAP that are quickly reclaimed
   show absorption, and the trend resumes (Shannon's "above rising VWAP =
   buyers in control"; SMB's "VWAP as the arbiter of intraday bias").
3. **Commonly accepted entry rules:** established buyer control (price above
   VWAP for most of the session so far — our 60% rule is a fair encoding, and
   a *rising* VWAP is required, not merely price above it); a dip through VWAP
   on *declining* volume; entry on the bar that reclaims VWAP (close back
   above), ideally with the index also above its VWAP; first or second reclaim
   of the day only.
4. **Commonly accepted exit rules:** scale into the prior high-of-day; 2R
   common; runners trail while VWAP rises; flatten if price re-loses VWAP
   decisively (the thesis is falsified); square-off by 15:15.
5. **Commonly accepted stop-loss rules:** below the dip's low (the excursion
   that was reclaimed) — our implementation — or a fixed small buffer below
   VWAP itself.
6. **Commonly accepted trailing methods:** under VWAP as it rises; or under
   successive higher lows. (Formal ATR trails are not part of this playbook.)
7. **Used when:** clear trend days — gap-and-hold opens, index trending, VWAP
   visibly sloped; normal-to-high RVOL sessions.
8. **Avoided when:** VWAP flat and price oscillating through it (the "VWAP
   magnet" range day — reclaim signals are noise); after VWAP has been lost
   and reclaimed 3+ times; last 45–60 minutes (no runway); big-gap fade days
   where VWAP is far from the open.
9. **Common confirmation filters:** VWAP slope up; dip on shrinking volume,
   reclaim on expanding volume; index above its VWAP; reclaim bar closing in
   its upper third.
10. **Common rejection filters:** reclaim count > 2 for the session; VWAP
    slope ~flat; entry after ~14:30; results day.
11. **Typical timeframe:** 5m/15m; VWAP session-anchored (ours resets daily —
    correct).
12. **Typical holding period:** 1–4 hours; flat by close.
13. **Typical instruments:** the most institutionally traded names (VWAP is
    only meaningful where institutions benchmark to it) — NIFTY-100 grade.
14. **Common mistakes beginners make:** buying every VWAP cross on a range
    day; ignoring slope; entering the 4th reclaim; no index check; stops at
    round numbers instead of the dip low.
15. **Already implemented:** session VWAP (correct reset); 60% prior-bars
    buyer-control gate; warmup gate; reclaim-cross entry; dip-low stop; 2R
    target; square-off; reclaim-strength/participation in `confidence()`.
16. **Important elements currently missing:** rising-VWAP requirement (we gate
    on share-above, not slope); flat-VWAP/range-day rejection; per-session
    reclaim-count cap; volume signature (dip contracting / reclaim expanding);
    index alignment; entry time cutoff; event avoidance.

## 3. VWAP Pullback — `vwap_pullback_15m`

1. **Original strategy name:** VWAP pullback / first test of VWAP as support.
2. **Canonical description:** on a trending session, the first orderly
   pullback to the rising VWAP is where under-invested participants buy the
   benchmark price; the low tags VWAP, holds, and the trend resumes (Shannon;
   standard prop curriculum "first VWAP test after a gap-and-go").
3. **Commonly accepted entry rules:** rising VWAP with price above; the FIRST
   (at most second) tag of VWAP of the session; tag bar holds (close above
   VWAP); pullback leg on *declining* volume; entry above the tag/resume bar's
   high; index constructive.
4. **Commonly accepted exit rules:** prior high-of-day first scale; 2R; trail
   under VWAP for runners; square-off by 15:15.
5. **Commonly accepted stop-loss rules:** just below VWAP or below the tag
   bar's low — whichever is structurally cleaner (ours: below VWAP frozen at
   entry — one of the two accepted forms).
6. **Commonly accepted trailing methods:** under rising VWAP / higher lows.
7. **Used when:** genuine trend days with institutional participation
   (gap-and-hold, index aligned, RVOL ≥ normal).
8. **Avoided when:** VWAP flat/rolling; the THIRD-plus test (each successive
   test weakens support — this is the single most repeated warning in VWAP
   practice); after a VWAP loss earlier in the session; midday drift tags on
   dead volume; late-day tags.
9. **Common confirmation filters:** first-test-of-day; volume contraction into
   the tag, expansion on resume; VWAP slope; index above its VWAP.
10. **Common rejection filters:** test count ≥ 3; flat VWAP; RVOL < ~1;
    post-14:30; results day.
11. **Typical timeframe:** 5m/15m.
12. **Typical holding period:** 1–3 hours.
13. **Typical instruments:** institutionally traded large-caps, strong gappers.
14. **Common mistakes beginners make:** treating every VWAP touch as a
    pullback (the exact failure our Phase-16 audit measured: our encoding
    over-fires 5–9× vs discretionary use); buying INTO the falling tag rather
    than on the resume; no volume check; third and fourth tests; range days.
15. **Already implemented:** rising-VWAP requirement (bar-over-bar diff);
    price-above requirement; 0.2% tag band with close holding; resume trigger
    (close above prior high); warmup; below-VWAP stop; 2R target; square-off.
16. **Important elements currently missing:** **first/second-test-only cap**
    (the highest-impact missing rule in the whole library — directly addresses
    the documented 5–9× over-firing); volume-signature filter; flat-VWAP
    rejection; prior-VWAP-loss disqualifier; index alignment; time cutoff;
    event avoidance.

## 4. CPR Breakout — `cpr_breakout_15m`

1. **Original strategy name:** Central Pivot Range breakout (CPR/TC breakout).
2. **Canonical description:** the CPR (pivot, BC, TC from the prior day's
   H/L/C) frames the day's expected value area; price accepting above the top
   central level signals a trend-up day (Ochoa, *Secrets of a Pivot Boss* —
   the framework Indian intraday educators teach almost verbatim).
3. **Commonly accepted entry rules:** **narrow CPR days only** — a CPR
   noticeably narrower than recent days (Ochoa's trend-day tell; commonly
   "width in the bottom ~20–30% of the trailing month", or width ≲ 0.2–0.5×
   ATR) — this is a *gate* in practice, not a bonus; two-day relationship
   bullish (today's CPR above or overlapping-higher vs yesterday's); open
   inside or modestly above value (not a huge gap); break above TC on RVOL;
   morning entries strongly preferred.
4. **Commonly accepted exit rules:** first target R1 (floor pivot), scale and
   move stop to breakeven; second target R2; on strong trend days trail the
   remainder; square-off by 15:15. (Exactly the ladder we implement.)
5. **Commonly accepted stop-loss rules:** below the CPR bottom (BC) for the
   standard trade (ours); below the pivot for wider variants; tighter
   below-TC stops on very narrow CPRs.
6. **Commonly accepted trailing methods:** stop to breakeven at R1 (ours);
   then under floor pivots / higher lows for the R2 leg.
7. **Used when:** narrow-CPR mornings with directional opens; virgin-CPR
   context (prior day's untested CPR below acts as support); index CPR
   agreeing.
8. **Avoided when:** WIDE-CPR days (rotational/rangebound expectation — the
   breakout playbook is explicitly wrong there; practitioners switch to
   fade-the-extremes); large gap opens far beyond the CPR (value is
   abandoned; different playbook); inside-CPR chop afternoons; event days.
9. **Common confirmation filters:** narrow CPR vs trailing widths; rising
   two-day CPR relationship; RVOL on the break bar; index above its own
   pivot/VWAP; break bar closing above TC decisively.
10. **Common rejection filters:** wide CPR; open gapped > ~1.5–2% beyond TC;
    post-lunch first breaks; third test of TC after two failures; results day.
11. **Typical timeframe:** 5m/15m; levels from daily H/L/C.
12. **Typical holding period:** 1–4 hours; flat by close.
13. **Typical instruments:** NSE F&O stocks and index futures — this setup's
    modern home is specifically Indian intraday trading.
14. **Common mistakes beginners make:** trading TC breaks on wide-CPR days;
    ignoring the gap context; no volume check; stop at the pivot when the CPR
    is narrow (risk out of proportion); chasing the 3rd midday re-break.
15. **Already implemented:** correct causal CPR math (prior session, tested);
    TC-break entry with mandatory volume; opening-range warmup gate; CPR-bottom
    stop; R1 partial (50%) → breakeven → R2 (the full Ochoa ladder); square-off;
    narrow-CPR scored in `confidence()`.
16. **Important elements currently missing:** narrow-CPR **gate** (the core
    selection rule of the method — currently only scored); two-day CPR
    relationship; gap-context rejection; virgin-CPR context; RVOL vs time of
    day; index alignment; morning-only entry window; event avoidance.

## 5. ORB Retest Continuation — `orb_retest_5m` (current canonical implementation)

STRAT-02 replaces the former 15-minute single-pullback approximation with a
bidirectional 5-minute state machine. It enforces buffered break state, 0.5 ATR
minimum extension, a 0.15% retest zone, 0.5 ATR depth and eight-bar duration
limits, directional continuation, VWAP/RVOL/scoring gates, a collared confirmed
fill, pivot/ATR stop, grade-dependent partial, breakeven, chandelier,
boundary/VWAP failure, and stagnation exit. See
`docs/STRAT02_ORB_RETEST_5M.md` for the complete contract.

### Retired `first_pullback_15m` practitioner comparison (legacy evidence only)

1. **Original strategy name:** first pullback after breakout (first flag;
   "ORB retest" family; Raschke's first-pullback principle: the first
   reaction after a momentum thrust is bought).
2. **Canonical description:** the initial breakout attracts momentum buyers;
   the FIRST shallow pullback that holds the breakout level proves demand;
   entering as it resumes offers trend continuation with defined risk
   (Raschke; Landry's pullback method adapted intraday; prop "first flag"
   convention).
3. **Commonly accepted entry rules:** a genuine breakout leg on RVOL; a
   SHALLOW pullback of 1–3 bars on *declining* volume that holds above the
   breakout level (and ideally above VWAP); entry via buy-stop above the
   pullback pattern's high (not just the single first down bar — practitioners
   let the pullback complete 1–3 bars and trigger above the little pattern);
   first pullback only; morning setups preferred.
4. **Commonly accepted exit rules:** 2R standard; or measured move of the
   impulse leg; runners on trend days; square-off by close.
5. **Commonly accepted stop-loss rules:** below the pullback's low (ours);
   aggressive variant: below the breakout level.
6. **Commonly accepted trailing methods:** breakeven after 1R; then higher
   lows.
7. **Used when:** strong directional mornings, index aligned, fresh breakout
   (first of the day), orderly tape.
8. **Avoided when:** the pullback is deep (gives back > ~50% of the impulse or
   loses the level/VWAP); the pullback stalls sideways > ~4–5 bars (momentum
   gone); it is the 2nd/3rd flag; late-day; parabolic first legs (no orderly
   flag forms).
9. **Common confirmation filters:** volume contraction in the pullback and
   expansion on the resume; higher low vs OR high (ours); VWAP beneath as
   support; index trend.
10. **Common rejection filters:** pullback depth/duration caps; flag count
    > 1 (ours enforces first-only — correct); post-14:00 resumes; results day.
11. **Typical timeframe:** 1m/5m (US momentum practice) to 15m (our NSE
    encoding).
12. **Typical holding period:** 30 minutes–3 hours.
13. **Typical instruments:** liquid momentum names; NSE F&O large-caps.
14. **Common mistakes beginners make:** buying the falling pullback bar
    instead of the resume trigger; taking every subsequent flag; ignoring the
    volume signature; entering flags that formed BELOW the breakout level.
15. **Already implemented:** OR-breakout precondition; first-pullback-only
    (session-scoped, correct); higher-low hold above the OR high; resume
    trigger above the pullback high; still-above-level check; one entry per
    session; pullback-low stop; 2R target; square-off.
16. **Important elements currently missing:** multi-bar pullback pattern
    (currently exactly ONE down-bar defines the pullback; the 2–3-bar orderly
    flag with entry above the pattern high is the commoner form); pullback
    depth/duration caps; volume-signature filter; RVOL on the impulse; index
    alignment; time cutoff; breakeven-after-1R.

## 6. EMA Pullback Continuation — `pullback_15m`

1. **Original strategy name:** intraday EMA pullback (trend-pullback
   template; Brooks' "high-2 buy at the EMA" family; Raschke's retracement
   entries; the ubiquitous 9/20-EMA continuation).
2. **Canonical description:** in an established intraday uptrend, pullbacks to
   the trend's own moving average are bought when they end, because trend
   participants defend their average entry (dynamic support).
3. **Commonly accepted entry rules:** a QUALITY uptrend (structure of higher
   highs/lows, or fast EMA above slow with clear separation — trend strength
   is a gate, commonly an ADX floor ≈ 20–30 or visible EMA separation);
   shallow touch/undercut of the fast EMA; entry on the reclaim/resume bar
   above the prior bar's high; index aligned; not overextended (price not
   already stretched far above the EMA when the trend began).
4. **Commonly accepted exit rules:** prior swing high first target; 2R; trail
   under higher lows; flatten on a close below the slow EMA (trend broken);
   square-off by close.
5. **Commonly accepted stop-loss rules:** below the pullback swing low (the
   structural form; our ATR/swing hybrid approximates it), not merely at the
   EMA.
6. **Commonly accepted trailing methods:** successive higher lows / fast EMA
   trail; breakeven after 1R. (Our chandelier+locks is a defensible
   equivalent; no single canon exists.)
7. **Used when:** trending sessions with orderly pullbacks, index trending
   the same way, normal+ RVOL.
8. **Avoided when:** flat/interlaced EMAs (chop — the well-known whipsaw
   killer); lunch hours; counter-index longs; late-day; after 3+ pullback
   cycles (mature trend).
9. **Common confirmation filters:** trend-strength floor (ADX/EMA separation);
   RSI holding above ~40 on the dip (our confidence band matches practice);
   volume expanding on resumes.
10. **Common rejection filters:** EMA separation ~0 (flat); entry when price
    is > ~2 ATR above the slow EMA (overextended); post-14:30.
11. **Typical timeframe:** 5m/15m.
12. **Typical holding period:** 45 minutes–3 hours.
13. **Typical instruments:** liquid trending large-caps.
14. **Common mistakes beginners make:** trading EMA touches in ranges;
    knife-catching the dip without the resume trigger; stops exactly at the
    EMA; ignoring the index; oversizing midday signals.
15. **Already implemented:** 20/50 EMA regime; recent-touch requirement;
    reclaim-cross entry; RSI/trend-separation/participation in
    `confidence()`; ATR/structure stop + chandelier trail (its owned profile);
    square-off.
16. **Important elements currently missing:** trend-strength **gate**
    (separation/ADX floor — currently only scored); overextension rejection;
    time-of-day window; index alignment; prior-swing-high target option;
    RVOL vs time of day.

## 7. Volatility/Bollinger Squeeze Breakout — `volexp_1h`

1. **Original strategy name:** Bollinger Band squeeze breakout (volatility
   contraction/expansion; TTM squeeze variant).
2. **Canonical description:** volatility cycles: when bands contract to an
   unusually low bandwidth, the subsequent expansion tends to be directional
   and tradeable; the first close outside the band in the squeeze's direction
   of resolution is the trigger (Bollinger; Carter's TTM squeeze adds a
   momentum-direction filter and BB-inside-Keltner definition).
3. **Commonly accepted entry rules:** squeeze defined RELATIVELY — bandwidth
   at/near its own N-period LOW (Bollinger explicitly defines the squeeze as
   bandwidth at a 6-month low, i.e. a percentile of its own history, never an
   absolute number — our fixed 0.03 threshold is a simplification he warns
   against, since it fires differently across price/volatility levels);
   direction confirmation (momentum histogram positive, or price structure/
   trend agreeing, or %b leading); volume expanding on the breakout bar;
   beware the "head fake" (Bollinger's own warning: the first break often
   reverses — practitioners demand the close outside the band, which we do,
   and many require the next bar to confirm).
4. **Commonly accepted exit rules:** ride the "band walk" while closes hold
   the upper half; exit on momentum flip or close back inside the midband;
   Carter: trail after the histogram peaks; intraday variants square off by
   close.
5. **Commonly accepted stop-loss rules:** midband (20-SMA) for conservative
   sizing; opposite band for room; below the squeeze's low structurally.
6. **Commonly accepted trailing methods:** midband trail; or higher lows.
7. **Used when:** quiet coiling markets about to receive flow (post-lunch
   coils into afternoon trends; multi-session coils on 1h charts); with-trend
   resolutions.
8. **Avoided when:** the compression is pre-scheduled-news (results, policy —
   the expansion is a coin flip through your stop); against the prevailing
   trend; illiquid names where the "squeeze" is just no trading.
9. **Common confirmation filters:** bandwidth percentile low; momentum/trend
   direction; volume surge on break; higher-timeframe trend agreement.
10. **Common rejection filters:** event within the coil; counter-trend break;
    third re-break after two failures.
11. **Typical timeframe:** 15m/1h intraday (ours: 1h), daily for swing.
12. **Typical holding period:** hours (intraday form); flat by close in MIS.
13. **Typical instruments:** liquid names/indices with real institutional
    flow.
14. **Common mistakes beginners make:** absolute bandwidth thresholds (our
    exact simplification); no direction filter; chasing the 3rd bar after the
    break; trading squeezes into earnings; confusing illiquidity with coiling.
15. **Already implemented:** BB(20,2σ) bandwidth; 3-bar prior-only squeeze
    window (breakout bar can't disqualify itself — correct); close-above-band
    trigger; squeeze-tightness/volume/close-location in `confidence()`; its
    owned ATR-stop + chandelier profile; square-off.
16. **Important elements currently missing:** RELATIVE bandwidth definition
    (percentile of its own trailing history — definitional fidelity to
    Bollinger, not a tuning knob); direction/trend filter; volume
    confirmation as a **gate** (currently confidence-only); event avoidance;
    midband-based stop option.

---

## 8. Phase 2 — comparison table (no code changed)

Priorities rank how much the missing elements separate our version from the
practitioner version (H = the gap changes what the strategy IS; M = the gap
loses consistently-recommended context; L = near-faithful already).

| Strategy | Current implementation | Common practitioner implementation | Missing elements | Implementation priority |
|---|---|---|---|---|
| `orb_5m` | Specification STRAT-01; see `STRAT01_ORB_5M.md` | Implemented | Optional spread/membership inputs substituted conservatively; sector cap awaits metadata | **Complete except documented sector-metadata dependency** |
| `vwap_pullback_15m` | Rising VWAP; 0.2% tag holding; resume trigger; below-VWAP stop; 2R; square-off | FIRST/second VWAP test only, on a real trend day, with volume signature and index confirmation | Test-count cap (fixes documented 5–9× over-fire); volume signature; flat-VWAP rejection; index gate; time cutoff | **High** — over-firing means we mostly trade setups practitioners refuse |
| `cpr_breakout_15m` | Causal CPR; TC break + volume; BC stop; R1 partial→BE→R2; square-off | Same trade but ONLY on narrow-CPR days with supportive two-day relationship and sane gap; morning entries | Narrow-CPR gate; two-day relationship; gap rejection; virgin-CPR context; RVOL; index gate; morning window | **High** — day-type selection *is* the CPR method |
| `vwap_15m` | 60% buyer-control; reclaim cross; dip-low stop; 2R; square-off | Reclaim on RISING VWAP, first/second reclaim only, trend-day context, volume signature | Slope requirement; reclaim-count cap; range-day rejection; index gate; time cutoff | **Medium** |
| `orb_retest_5m` | Specification STRAT-02; see `STRAT02_ORB_RETEST_5M.md` | Implemented | Optional spread/membership inputs substituted conservatively; sector cap awaits metadata | **Complete except documented sector-metadata dependency** |
| `pullback_15m` | EMA20/50 regime; touch + reclaim; ATR/swing stop + chandelier | Same template but gated on trend STRENGTH, not merely EMA order; overextension rejection; index aligned | Trend-strength gate; overextension rejection; index gate; time window | **Medium** |
| `volexp_1h` | Absolute bandwidth ≤0.03 for 3 prior bars; close above band; ATR/trail | Bandwidth at a RELATIVE low of its own history; direction filter; volume gate; event skip | Relative-bandwidth definition; direction filter; volume gate; event skip | **Medium** (the definition item is faithfulness, not tuning) |

**Shared missing infrastructure** (needed by the H-priority gates, in
dependency order): (1) index intraday data — none exists in the store today;
(2) a time-of-day RVOL helper; (3) an earnings/results calendar for event
avoidance (no source wired today). See the data capability report and the
roadmap.
