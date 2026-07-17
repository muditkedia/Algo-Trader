# Learnings

Empirical findings from validation. Newest first. Numbers are net of 0.1%/side
fees, Binance spot, BTC+ETH, dev corpus 2020-01..2025-06 unless noted.
L-010+ are Indian equities (NSE, 99 NIFTY-100 symbols, 2023-01..2026-07,
NseEquityCostModel).

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
