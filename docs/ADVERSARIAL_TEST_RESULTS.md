# ADVERSARIAL TEST RESULTS

_Independent verification phase, 2026-07-19. New suite:
`tests/test_adversarial_verification.py` (46 tests) + 2 regression tests for
the fixes in `tests/test_new_intraday_strategies.py`. Full suite after the
phase: **489 passed, 1 skipped** (was 441)._

## Pathological cases attempted, and outcomes

| Case | Result |
|---|---|
| Empty dataset (0 bars) through every intraday strategy | survives via warmup gates when dtypes are proper; **store's object-dtype empty frame raises in `prepare` (finding F8 — all platform callers guard with `bars.empty` first; no production path affected)** |
| Single-bar / 2-bar / 5-bar datasets, all 12 strategies | no exception, zero signals (12 × 4 parametrized) |
| Single-bar dataset in the engine | untradeable → `None`; 2-bar dataset → enters and squares off on bar 2 |
| Very short histories (≤ min_history) | gated to zero signals |
| Missing bars mid-session (2-hour hole) | date-driven session logic squares off on the last PRESENT bar |
| Duplicate bars / duplicate timestamps | no crash in strategies or engine (store dedup prevents them upstream anyway) |
| Zero-volume bars | execution unaffected (volume never read by the engine) |
| Invalid OHLC (high < low, open outside range) | deterministic, no crash |
| NaN bar mid-trade | trade completes; P&L, MFE and MAE stay finite |
| Flash-crash candle (−20% open) | honest fill at the open, `gap_fill` flagged, P&L exact |
| Simultaneous stop and target on one bar (incl. an insane bar gapping above target while trading below stop) | stop-out — the pinned pessimistic convention |
| Large opening gap through a swing stop | fill at the day's open, not the stop |
| Consecutive gap days (swing) | survives day 1, exits honestly at day 2/3 open |
| Muhurat-like 2-bar evening session | enters, squares off in 15 minutes |
| Market holiday between sessions | prior-day levels reference the prior TRADED session |
| Signals during square-off (last bar) | untradeable by construction (existing engine test) |
| NaN stop level / zero ATR | signal skipped honestly, no crash |
| Extreme slippage (100 bps/side) | net = gross − cost holds linearly |
| Partial exit on the last manageable bar | partial books + same-bar square-off; per-leg costs verified against the real NSE model |
| Corporate-action-like price jumps | equivalent to the gap cases above at the engine level; unadjusted-data caveat remains a DATA property (documented in the data reports) |
| IPO short history / delisting mid-frame | short-history = warmup-gated; frames simply end (data-end square-off tested) |
| Statistics battery on a hand-computed trade set | net, PF, win rate, expectancy, max-drawdown all exact (expectancy rounded to 6dp for reporting) |

## Real-data quantifications that drove fixes

Same-session re-entry measurement (10 liquid symbols × ~9.7 years, 15m):

| Strategy | Sessions w/ signal | Sessions w/ MULTIPLE entries | After fix |
|---|---|---|---|
| nr7_intraday_15m | 1,626 | **592 (36.4%)** | **0** (signals 2,553 → 1,626) |
| gapgo_15m | 80 | **9 (11.2%)** | **0** (signals 89 → 80) |
| orb_15m | 5,411 | 481 (8.9%) | unchanged — finding F3, owner decision |
| cpr_breakout_15m | 2,857 | 234 (8.2%) | unchanged — finding F3 |

## Hypotheses tested and FALSIFIED (no bug found)

- **Supertrend warmup phantom flip**: suspected a spurious signal where the
  direction series transitions from its pre-warmup state. Empirically
  falsified — Wilder EWM warms from bar 0, so no zero-state boundary exists;
  a monotonic uptrend from series start produces ZERO signals (now a
  pinned regression test).
- **NaN propagation into MFE/MAE**: argument order in the running max/min
  protects the accumulators; verified by test.
- **Session-boundary leakage of the inside-bar pattern**: `groupby(day)`
  ffill cannot cross sessions; a pattern formed at the close does not arm
  the next morning (pinned test).

## Deliberately pinned limitations (tests assert CURRENT behaviour)

- NR7 gate fires after a special short session (Muhurat) — finding F4;
  designed fix is the future session-type feature.
- Same-bar pessimism and partial-then-breakeven sequencing — conventions,
  documented in the engine audit.
