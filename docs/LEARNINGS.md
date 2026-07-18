# Learnings

Empirical findings from validation. Newest first. Numbers are net of 0.1%/side
fees, Binance spot, BTC+ETH, dev corpus 2020-01..2025-06 unless noted.
L-010+ are Indian equities (NSE, NseEquityCostModel). L-012+ use the expanded
corpus (500 NIFTY-500 symbols, 2015-01..2026-07).

---

## L-016 — Close-of-bar entry is a hidden ~15–45 bps tax; a backtest tests its execution model as much as its strategy (2026-07-18)

The Phase-16 fidelity audit (D-036) decomposed the five intraday baselines and
found the platform's OWN execution conventions dominate the result:

- **Gross expectancy at the fill is ~zero for all five** (−1.6..+3.1 bps): net
  loss = the 12.2 bps cost stack on zero-edge fills. L-006's arithmetic, fourth
  market/framework combination in a row.
- **The close-of-bar entry convention pays a measured +14..+44 bps "chase"**
  above the published trigger level (stop-order execution). For breakout-style
  entries this single convention costs MORE than the entire cost stack — the
  strategy fires at the level, but the platform buys the close of a bar that has
  already run. Every level-triggered backtest on this platform embeds this tax.
- **The uniform targetless exit makes the modal trade a costed noise
  round-trip**: 83–91% of intraday trades hit neither stop nor trail and ride to
  square-off at −2..−7 bps. Stops are 5–13% at ~−100 bps (0% win by
  construction); the trail banks +34..+57 on only 3–6.5%. That shape — not any
  directional signal — produces the "every month slightly negative" pattern
  (214/215 negative strategy-months).

**General lessons:** (1) a backtest verdict is a joint test of signal AND
execution model — audit the execution model BEFORE optimising the signal, or the
optimiser will learn to offset platform artifacts; (2) mechanising a
discretionary setup can silently change its trade count by 5–9× (vwap_pullback)
— trade-frequency vs the source's described cadence is a cheap fidelity check;
(3) characterization tests that PIN execution semantics (entry timing, stop-gap
fills, target absence) make audit claims falsifiable and permanent.

**How to apply:** before any intraday improvement work, decide the fidelity
evaluation mode (trigger-level entries, published stops/targets) so improvements
are measured against the published economics, not against the chase artifact.
Never optimise a strategy on a baseline whose largest P&L component is an
execution convention. See [[real-measurement-verdict]] and
[[crypto-validation-lessons]].

---

## L-015 — Even maximum-power short-horizon per-trade effects do not certify on NSE (2026-07-18)

R-001 executed (D-035): 3 pre-registered short-horizon families on NIFTY-500,
frozen gate. The decisive result is the F&O **expiry** effect — the best
short-horizon result the project has produced:

| family | selection point | CI-low | beats random? | verdict |
|---|---|---|---|---|
| expiry (F&O expiry week) | **+74.4** | **−1.6** | yes (rel-PF 1.35) | FAIL (inconclusive) |
| month-start (SIP inflow) | +18.0 | −21.3 | no | FAIL (rejected) |
| gap-fade (down-gap reversal) | **−44.7** | −167 | no | FAIL (falsified) |

Expiry has a +74 bps point edge that CLEARS the 30.9 cost, beats a random entry,
and rests on 54,000 signals at a 3-7-day horizon — the most powered configuration
the platform can build. Its selection CI-low is still −1.6: not distinguishable
from zero at 95%, let alone from cost. **If the most-powered per-trade
short-horizon effect on the platform cannot clear even the zero line, per-trade
short-horizon equity selection on NSE has hit its drift/noise floor.** More
signals will not fix it — 54k already exhausts the sample.

Two clean secondary findings:
- **NSE gaps show downward continuation, not mean-reversion:** down-gaps continue
  (gap-fade −45) and up-gaps don't continue up (egap −84). Gaps are not a
  tradeable reversal on NSE.
- **The turn-of-month edge is anticipatory:** the pre-month-END entry (tom, +75)
  beats the month-START inflow arrival (month-start, +18). The market front-runs
  the known flow.

**How to apply:** stop searching for a per-trade short-horizon selection edge —
the expiry result is the ceiling and it does not certify. The directionally-real
effects (expiry, and the batch-2 factors) can now only be pursued as PORTFOLIOS
(R-002): a monthly expiry/factor basket's ~130 near-independent monthly returns
may certify where the per-trade CI cannot. This is the one remaining route that
does not touch the frozen gate. See [[long-horizon-gate-null-failure]].

---

## L-014 — Governance is now the permanent mode; throughput raises the false-discovery risk (2026-07-18)

Phase 13 established permanent research governance (`docs/RESEARCH_REGISTRY.md`,
`docs/RESEARCH_STANDARDS.md`; D-034). The load-bearing process learning behind it:
the project's pivot to high-throughput hypothesis screening (Phase 12 framework)
is exactly the activity that manufactures false discoveries. L-010 already proved
the danger empirically — random entries passed an un-benchmarked gate. Screening N
ideas at 95% confidence yields ~N/20 false PASSes by chance.

**So the two lessons that cost the most are now encoded as weighted priorities and
standing controls, not just prose:** the Hypothesis Quality Framework weights
expected statistical POWER (×3, from L-012 — horizon beats sample size) and
INDEPENDENCE from rejected ideas (×2.5, from the wasted re-tests) above
theoretical fame or novelty; and the standards mandate that the registry's
tested-count is the multiple-testing denominator, a single PASS is provisional
pending pre-registered out-of-sample confirmation, and negative results are
archived (never re-proposed, never deleted).

**How to apply:** before any screening batch, reserve an out-of-sample holdout and
state the expected hypothesis count; report the full selection-edge DISTRIBUTION,
not just the max; treat the best result as provisional until confirmed on held-out
data. Governance is not bureaucracy here — it is the direct countermeasure to the
specific failure mode (false discovery) that the project's new speed creates. See
[[long-horizon-gate-null-failure]].

---

## L-013 — The 27-strategy post-mortem: what the whole search taught (2026-07-18)

After 27 strategies across every major hypothesis family, 0 certified. The
process-level learnings (`research/POSTMORTEM.md`):

1. **Relative rank carries signal; absolute level does not.** Every
   cross-sectional RANK signal (momentum, RS, beta, liquidity, stage) has a
   positive point selection edge; every per-symbol LEVEL/own-trend signal
   (tsmom sign, ema200, vwap, hi52 threshold) has ~zero or negative. If a signal
   can be earned by a rising tide, the drift-adjusted gate strips it. **Rank
   against peers, don't threshold against a level.**
2. **The horizon-power tradeoff is THE organising axis.** Selection edge grows
   with the mechanism (cross-sectional rank) but the CI grows with the horizon,
   and horizon wins: illiq's +392 bps point over 126d is less certifiable than
   tom's +75 over 3-7d. Effective n is the independent-BLOCK count. **Choose the
   horizon where the sample is powered, not where the thesis is prettiest.**
3. **Directional truth is not tradeable edge, and the gate is right to say so.**
   12/13 cross-sectional factors are directionally real and beat random on
   average, yet none certifies — this is the frozen gate correctly declining to
   bless economically-large but statistically-unresolved edges. A discipline,
   not a defect.
4. **Falsified on THIS sample:** the low-volatility anomaly (inverted); that a
   bigger corpus certifies long-horizon factors (D-032); price-only gaps as a
   PEAD proxy. **Still untested:** true event-driven, portfolio-level factor
   evaluation, short-horizon microstructure beyond tom.
5. **Manual per-strategy research is exhausted.** The 27th hand-written strategy
   taught almost nothing new; the map is drawn. The next era is DECLARED
   hypotheses (the Phase-12 framework) screened at powered horizons, plus
   acquiring event data — not more bespoke strategy classes.
6. Confidence heuristics carry no signal for the FOURTH time — stop building
   them; if signal quality is ever modelled it must be learned from evidence,
   never hand-designed.

**How to apply:** pivot to automated hypothesis generation/screening at
short/powered horizons; pursue portfolio-mode evaluation as the one route that
could certify the directionally-real factors WITHOUT touching the frozen gate;
acquire earnings/index-event data to unblock the strongest untested anomaly. Do
not write more per-symbol technical strategies or re-test long-horizon single
factors. See [[long-horizon-gate-null-failure]] and [[crypto-validation-lessons]].

---

## L-012 — Cross-sectional factors are DIRECTIONALLY real on NSE but not certifiable at long horizons (2026-07-18)

Batch 2: 13 cross-sectional / factor strategies on the 500-symbol × 11-year
corpus, frozen D-031 gate. All FAIL — but differently from batch 1:

| strategy | selection POINT | selection CI-low | excess-vs-random | rel PF | verdict |
|---|---|---|---|---|---|
| illiq | +392 | −1070 | +0.0039 | 1.19 | FAIL |
| stage2 | +383 | −883 | −0.0014 | 0.93 | FAIL |
| hi52rank | +252 | −762 | +0.0004 | 1.02 | FAIL |
| bab | +232 | −854 | +0.0157 | 1.79 | FAIL |
| breadth_regime | +191 | −310 | +0.0021 | 1.10 | FAIL |
| xsmom / dualmom | +149 | −343 | +0.0017 | 1.05 | FAIL |
| resmom | +112 | −363 | +0.0018 | 1.08 | FAIL |
| **tom** | **+75** | **−7.9** | +0.0067 | 1.46 | FAIL |
| xsrev | +24 | −111 | +0.0007 | 1.02 | FAIL |
| maxret | +9 | −113 | +0.0001 | 1.02 | FAIL |
| combo | +33 | −329 | −0.0014 | 0.94 | FAIL |
| lowvol | −29 | −130 | −0.0041 | 0.82 | FAIL |

**Three findings:**

1. **The effects are directionally present.** Unlike batch 1's mixed-sign
   point edges, batch 2's cross-sectional selection edges are large, positive
   and CONSISTENT (12 of 13 positive), and 11 of 13 beat the random baseline on
   average (rel PF > 1). Cross-sectional momentum, 52-week-high rank, liquidity
   and stage effects carry the sign the literature predicts, on NSE. **A
   FAIL under the gate is a POWER verdict here, not "no effect".**

2. **More data did not close the CIs at long horizons.** 3.2× the independent
   periods and 4.7× the breadth, yet the 60-126-day selection CIs are as wide
   as ever (−300 to −1070 bps). At those horizons the per-period variance
   (2015-2026 incl. COVID) swamps the ~150 bps edge, and the independent
   long-horizon block count is irreducibly small on one 11-year market. **The
   binding constraint is horizon, not sample size** — you cannot buy power for
   a 6-month bet by adding stocks that all move together.

3. **Short horizon + many signals is where the gate has power.** tom_daily
   (3-7 day hold, 53,645 signals) has CI-low −7.9 bps — a hair from
   certification and the closest of all 26 strategies ever tested. The
   cross-sectional long-horizon factors, despite far larger POINT edges, have
   CIs two orders of magnitude wider. **Effective n is the independent-block
   count, and short horizons maximise it** (L-011 restated at the batch level).

**Also:** every batch-2 strategy loses to buy & hold (long-only can't beat an
11-year bull by holding a decile), and lowvol's NEGATIVE point edge says the
low-volatility anomaly is INVERTED on this momentum-led NSE sample. Confidence
heuristics again carry no signal (|corr| ≤ 0.06) — L-003 a fourth time.

**How to apply:** stop testing multi-week/month factor sorts — the data cannot
power their CIs, whatever their point edge. Pursue SHORT-horizon high-frequency
effects (the tom lead) where the sample certifies. Do not deploy directionally-
real-but-uncertified edges; that is an explicit owner risk decision the frozen
gate deliberately declines to make.

---

## L-011 — A POINT edge is not an edge: the selection edge vs random is within noise for all 14 (2026-07-17)

The benchmark amendment (D-031) replaced the absolute gate with the SELECTION
edge (forward return minus a random entry from the same corpus), gated on the
block-bootstrap CI of the DIFFERENCE. Re-judging the whole database:

| strategy | absolute edge (bps) | selection POINT | selection CI-low | verdict |
|---|---|---|---|---|
| hvol_daily | 255.6 | +109.5 | **−82.3** | FAIL |
| wyckoff_spring_daily | 332.3 | +64.0 | **−108.5** | FAIL |
| triple_screen_daily | 179.5 | +23.5 | **−128.9** | FAIL |
| donchian55_daily | 564.7 | +22.9 | **−380.0** | FAIL |
| ema200_daily | 60.1 | +12.4 | −26.5 | FAIL |
| tsmom_daily | 313.0 | −19.1 | −70.7 | FAIL |

**Every apparent edge collapses.** The L-010 diagnostic reported selection
POINT estimates (+67/+91 bps) as if they were findings; with a proper difference
CI, none clears zero. Cause: at 20–60-day horizons the forward return — for BOTH
the strategy and the random baseline — is dominated by which multi-week window
it falls in, and 3.5 years holds only ~14–40 independent such windows. A ~60 bps
selection signal cannot be resolved from zero against that between-period
variance. The point estimate is real arithmetic; it is not evidence.

**The general lesson:** on short samples at long horizons, a benchmark-relative
POINT estimate is meaningless without its interval — and the interval is wide
because independent observations, not trades, are what pin down a mean. 3,769
wyckoff trades span ~40 independent 20-day blocks; the effective n is 40, not
3,769. This is L-009's day-clustering lesson (D-028) taken to its conclusion at
the difference level.

**Corollary — the drift decomposition.** Of wyckoff's 332 bps absolute edge,
~268 is the random-entry baseline (drift) and ~64 is selection; of donchian's
565 bps, essentially ALL is drift (selection +23, CI-low −380). Absolute edge at
long horizons is mostly beta. The gate now says so.

**Methodological note (the residual weakness):** the two-sample difference is
UNPAIRED, so it carries the drift variance of both samples. A paired date-matched
cross-sectional edge (return minus same-day universe mean) would cancel drift
per-observation and have far more power. Recommended as the next amendment, NOT
adopted post-hoc (that would be tuning the method to the answer).

---

## L-010 — THE FROZEN GATE PASSES PURE RANDOMNESS at multi-week horizons on this corpus (2026-07-17)

Batch-1 due diligence: a seeded strategy firing on ARBITRARY bars (no market
information), run through the identical pipeline at (10,20,40,60)-bar horizons
with identical management and costs, over 3 seeds:

| seed | verdict | PF | expectancy | "edge" | CI-low | vs random |
|---|---|---|---|---|---|---|
| 1 | **PASS** | 1.39 | +0.0044 | 503 bps | 227 bps | +14 |
| 2 | BORDERLINE | 0.98 | −0.0003 | 334 bps | 35 bps | −168 |
| 3 | **PASS** | 1.34 | +0.0046 | 473 bps | 130 bps | −12 |

**Two of three coin flips PASS the pre-registered bars.** Cause: the D-007 gate
hurdles ABSOLUTE forward return against cost. At 8-bar/intraday scale (where
the bars were designed and correctly rejected six strategies plus the noise
control) drift over the window is negligible; at 40–60 days on a 2023–26 bull
sample of CURRENT index constituents (survivorship), drift alone is 300–550 bps
— an order of magnitude above the 61.8 bps hurdle. The gate saturates on beta.

**Corollary (the useful signal that remains):** the edge lab's random-entry
baseline decomposes gross return into drift + selection. Batch-1 selection
edges (gross − random, best horizon): hvol +91 bps and wyckoff_spring +67 bps
(both > the 30.9 bps round trip); triple_screen +22 (below cost);
donchian −2; tsmom −146 (its entire 313 bps "edge" was drift — it picked
WORSE-than-random bars); squeeze −202 (compression actively selects bad bars,
exactly the pre-registered failure mode); egap −152; hi52 −227.

**Implications:** (1) no batch-1 verdict is deployment evidence; the paper
engine must not be started although three strategies now hold `measured`
status. (2) The acceptance protocol needs an owner-approved amendment (D-003:
VALIDATION_RULES is frozen): a drift-adjusted leg — selection edge vs the
random baseline must clear cost on its CI — plus seeded random-entry controls
run alongside every real measurement. (3) Confidence heuristics again carry no
signal (corr −0.04..+0.06; L-003 three times running).

---

## L-009 — THE ENTRY EDGE, MEASURED DIRECTLY: real, significant, and too small (2026-07-14)

1,699 entries (profile gate + full DecisionEngine GO, exits ignored), 2020-01..2025-06.

**Forward return, gross vs the 0.20% round-trip fee:**

| horizon | gross mean | day-clustered 95% CI | net mean | median (gross) | P(>0.2%) |
|---|---|---|---|---|---|
| 30 min | +1.5 bps | [-1.2, +4.1] | **-18.5 bps** | **-0.014%** | 27.4% |
| 60 min | +4.4 bps | [+0.1, +8.5] | **-15.6 bps** | **-0.016%** | 32.8% |
| 90 min | +5.4 bps | [-0.7, +11.2] | **-14.6 bps** | **-0.024%** | 35.1% |
| 120 min | +7.6 bps | [+0.4, +14.8] | **-12.4 bps** | **-0.032%** | 36.0% |
| 180 min | +13.3 bps | [+3.7, +22.7] | **-6.7 bps** | **-0.051%** | 38.3% |

* MFE/|MAE| (180m) = **1.17** for entries vs **0.97** random - a real favorable skew.
* Edge vs random entries: +0.9 to +9.2 bps, growing with horizon.
* **The edge is statistically significant** at 60/120/180 min (CI excludes zero).
* **The edge NEVER beats fees.** The lower 95% bound of the gross mean does not
  exceed the 20 bps cost at ANY horizon. Net expectancy is negative everywhere.
* **The MEDIAN entry loses money even before fees at every horizon.** The
  positive mean is carried entirely by a thin right tail; only 38% of entries
  clear +0.2% even at 180 min.
* Edge GROWS with horizon (1.5 -> 13.3 bps over 30 -> 180 min). The only regime
  where this signal could plausibly clear costs is holding periods far longer
  than the frozen SS7 band (30-120 min) - an owner decision, not a code fix.

**Conclusion: the entry is not mis-calibrated, it is under-powered.** No exit,
risk, or portfolio engineering can rescue a signal whose gross expectancy sits
below the transaction cost. The entry logic must be replaced.

---

## L-008 — v3 Gate 0 flag was a FALSE POSITIVE; all results are valid (2026-07-14)

Definitive reproduction (`gate0_investigation.py`): rebuilt the analyzed frame
on full data vs data **physically truncated** at the flagged candle
(ETH/USDT 2022-10-05 19:15).

* **All 37 signal-relevant columns x 88,936 candles: BIT-IDENTICAL** (atol 1e-12).
* Entry and exit signals identical at the boundary.
* The only "differences" were `date_15m/1h/4h` merge-bookkeeping columns, all
  `NaT != NaT` artifacts at the SERIES HEAD (warmup), not the cut boundary.

=> **(a) genuine strategy bias: RULED OUT.** Deleting all future data changes
nothing. **(b) numerical boundary effect: RULED OUT.**
**(c) Freqtrade tool artifact: CONFIRMED.**

**Mechanism:** freqtrade's `report_signal` re-runs a backtest with data cut at
the trade, and flags "bias" if the trade fails to reproduce. In the cut run an
informative timeframe can come back empty; our `populate_indicators` then hits
`if informative.empty: continue`, which **silently drops the `_4h` columns**;
the profile's `_missing_columns` guard then suppresses ALL entry signals, so the
trade cannot reproduce -> false bias flag. The differing column set then crashes
freqtrade's `analyze_indicators` (`DataFrame.compare` needs identical columns).
v1/v2 never crashed only because they never flagged a trade, so they never
entered that code path.

**Therefore v1, v2 and v3 Mode A results are all lookahead-free and VALID.**
(Latent robustness flaw recorded, NOT fixed: `populate_indicators` should not
silently emit a different column set when informative data is missing.)

---

## L-007 — v3 Gate 0 flag (superseded by L-008 - resolved as false positive)

v3 recursive-analysis passed, but **lookahead-analysis flagged a bias trade**
(ETH/USDT 2022-10-05, idx 194) and then **crashed** in Freqtrade's own
`analyze_indicators` (pandas `DataFrame.compare` label mismatch). The crash path
is only reachable when a bias IS flagged - v1/v2 had zero findings and never hit
it. Per VALIDATION_RULES SS17 this **voids v3's downstream numbers**.

**Process lesson:** a Gate 0 crash is not a Gate 0 pass. Any exit condition built
on a knife-edge threshold comparison (`close < ema_slow_15m`) is a candidate for
boundary sensitivity between full and truncated runs. Resolve before trusting.
All v3 figures in L-005b/L-006 are therefore **provisional**; the conclusions
they support are independently corroborated by v1 and v2 (both Gate 0 clean).

---

## L-006 — THE DECISIVE FINDING: all three versions are gross-flat; fees are the edge killer (2026-07-14)

`gross = net + fees` across the identical dev corpus:

| | trades | fees | net | **gross** | gross/trade |
|---|---|---|---|---|---|
| v1 | 890 | 178 | -152 | **+25.5** | +2.9 bps |
| v2 | 2,576 | 514 | -548 | **-33.7** | -0.1 bps |
| v3 | 2,093 | 418 | -421 | **-3.3** | -0.0 bps |

**Every version is roughly break-even BEFORE fees and loses by roughly the fee
bill.** The per-trade gross edge (~3 bps at best) is ~7x smaller than the 20 bps
round-trip cost. This confirms Phase E (L-003) at the P&L level: **no exit
redesign can create edge.** Exit tuning only shuffles P&L between exit buckets.

**Corollary - the trail/stop asymmetry (v3):** win rate reached 54.8% (best of
all three; trailing exits 80% win), yet PF is still 0.60 because the average
win is **+0.48 USDT** while the average loss is **-1.03 USDT**. The tightened
trailing (1.25xATR) cuts winners short while the initial stop (2.0xATR) lets
losers run full-size. At a 2:1 loss:win ratio you need ~68% wins to break even.
Tightening the trail raises win RATE but shrinks win SIZE - it cannot fix the
asymmetry on its own.

**Implication:** the project's binding constraint is not exits. It is that the
entry edge is an order of magnitude below transaction costs. v4 must either
find a materially larger per-trade edge, or trade far less often for far larger
moves - not tune exits.

---

## L-005b — v3: structural exits fail on EVERY timeframe tested (2026-07-14)

The single-variable v3 test (v2 with the 5m structural exit replaced by a
confirmation-based 15m exit) produced:

| structural exit | fires | net | win% | lag from peak |
|---|---|---|---|---|
| v1 (1h EMA family) | 75 | -56 | **0%** | 250 min |
| v2 (5m EMA cross) | 1,469 | -897 | **0.1%** | 38 min |
| v3 (15m confirmed) | 63 | -82 | **0%** | **605 min** |

* vs the 5m version: the 15m exit **fixed the catastrophe** (fires 23x less;
  net -897 -> -82; overall net -548 -> -421, PF 0.47 -> 0.60, win 32% -> 55%,
  MaxDD 55% -> 43%). The noise-ejection problem is solved.
* vs the 1h version: it did **NOT** improve. My design goal ("exit materially
  earlier than 1h") FAILED - requiring a full 15m EMA death-cross PLUS price
  confirmation is a *slow* condition; it fires at a **605-min** lag, later than
  v1's 1h exit (250 min), on trades already held ~13h and deeply underwater.
* **Three timeframes, three structural exits, 0% win rate every time.** Not one
  structural exit has ever produced a winning trade.

**Conclusion: remove structural exits entirely in v4.** The trailing stop is the
only profitable exit (v3: +553, 80% win).

**Where the loss went:** with no hair-trigger exit ejecting trades, losers now
ride to the full initial stop - which became the dominant bleed (598 fires,
**-892 USDT**, vs 314/-370 in v1). Higher turnover (2,093 vs 890 trades, from
the tighter trailing freeing capital) amplified both stop-outs and fees.

---

## L-005 — v2 experiment: two changes helped, one destroyed the strategy (2026-07-14)

Head-to-head, same window/config/fees:

| Metric | v1 | v2 |
|---|---|---|
| Trades | 890 | 2,576 |
| Win rate | 40.3% | 32.4% |
| Net USDT | -152 | **-548** |
| CAGR | -2.96% | -13.45% |
| Sharpe | -1.26 | -7.25 |
| Sortino | -2.58 | -13.60 |
| Profit factor | 0.68 | 0.47 |
| Expectancy | -0.17% | -0.21% |
| Max drawdown | 15.9% | **55.1%** |
| Mean MFE | +1.15% | +0.65% |
| Winner MFE capture | 43% | 40% |
| Median hold | 140 min | 50 min |

**By exit reason (v2):**
- `trailing_stop_loss`: 1,058 trades, **+421 USDT, 78.7% win** (v1: 501, +274, 72%). Tighter trailing WORKED.
- `stop_loss`: 49 trades, -72 (v1: 314, -370). Anti-chase + cost gate WORKED - far fewer bad initial stop-outs.
- `v2_exit_5m_trend_reversal`: **1,469 trades, -897 USDT, 0.1% win.** The new exit DESTROYED the strategy.

**Lesson:** exit *sensitivity* is a knife-edge. v1 exits were too slow (1h,
~225min lag, gave back gains). v2's 5m EMA-cross exit was too fast (fires on
routine pullbacks, ejects at a small loss, kills trend participation - MFE
1.15% -> 0.65%). The answer is neither: **let the (tightened) trailing stop be
the primary exit** and use, at most, a much slower/confirmed structural exit.

**Lesson:** a whole-strategy "FAIL" verdict can hide net-positive subsystems.
Isolating by exit reason showed the trailing engine (+421) and entry filters
(-72 vs -370) are beneficial; only the 5m structural exit is destructive.

---

## L-004 — Root cause of v1 failure is EXITS, not entries (Phase D, 2026-07-14)
v1 gives back ~1,180 USDT of favorable excursion (8x the net loss); winners
capture only 43% of MFE; objective exits fire ~225 min after the peak (0% win).
Entries reach positive MFE 68% of the time - the strategy is not directionless,
it bleeds through exits. See D-004.

## L-003 — There IS a small entry edge, below cost (Phase E, 2026-07-14)
Actual entries beat random by **+17.5 bps of 3h MFE (95% CI +7.6..+28.3)**;
gross expectancy +3 bps. But round-trip fees are 20 bps and exits capture 43%.
The edge is real but must be ~tripled in captured terms to clear costs.
Feature importance (AUC/Spearman/KS/quintile): only `duration` (outcome-side)
and weakly `adx_15m` carry signal; RSI/volume/volatility/RR/conviction and most
classic TA features are dead weight (AUC ~0.5). Chasing (`mom_1h`,
`dist_fast_5m`) is weakly harmful. More price-derived TA is NOT the fix.

## L-002 — v1 loses in every regime, including bull (Phase C, 2026-07-14)
Bull PF 0.73, range 0.71, bear 0.37 - all < 0.8. Not a regime-classification
problem (bull correctly dominates 515/890 trades); bull losses trace to exit
give-back (bull trades reach +1.26% MFE, realize -0.15%).

## L-001 — Uncalibrated defaults fail decisively but safely (Phase C, 2026-07-14)
v1 Mode A: PF 0.68, negative expectancy across the entire MC 95% CI, but MaxDD
contained at 15.9% and worst-case simultaneous stop 1.2%. The risk framework is
sound; the edge is not there yet. Gate 0 (lookahead + recursive) clean.
