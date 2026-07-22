# STRAT-02 — 5-minute ORB Retest Continuation

_Implemented 2026-07-23 from the master intraday strategy specification._

`orb_retest_5m` is the sole registered OR-boundary retest strategy. It replaces
the retired long-only `first_pullback_15m`; its historical research results
remain legacy evidence and are not attributed to STRAT-02.

## Trading contract

- Build the first 5-minute opening range and record the first 0.1%-buffered
  long breakout or short breakdown.
- Require the initial wave to extend at least 0.5 ATR, then retest the broken
  boundary within 0.15% without any close penetrating more than 0.5 ATR back
  inside the range.
- Invalidate a retest after eight bars. Enter from 09:30 through 14:45 IST only
  when a directional candle closes beyond the previous bar, VWAP aligns,
  same-slot RVOL is at least 1.75 long or 2.25 short, and regime/confidence
  thresholds are met.
- Use a 0.1% trigger-centered limit collar and create a position only after a
  confirmed fill.
- Stop beyond the retest pivot by 0.2 ATR, capped to 1.25 ATR from entry. Exit
  50% at 1.5R (1.0R for Grade C), move the runner to breakeven, then trail at
  2 ATR. Exit on combined boundary/VWAP failure, eight bars without 0.5R
  progress, or the session square-off.
- Risk is capped at 1% of equity and 20% capital allocation. Candidate priority
  is `0.65 × confidence + 0.35 × regime`.
- STRAT-01 and STRAT-02 cannot be active concurrently on the same symbol. A
  retest remains eligible after a stopped STRAT-01 position has closed.

The state machine is derived causally from completed session history. This
provides deterministic restart recovery without a second mutable strategy
state file.

## Shared infrastructure

STRAT-01's completed-session liquidity/NATR calculations, completed 15-minute
NIFTY trend, cross-sectional breadth, same-slot RVOL, collared fill lifecycle,
directional execution, partial/breakeven/chandelier management, risk grading,
and telemetry are reused. STRAT-02 adds reusable completed 15-minute session
aggregation/ADX, directional structural stop columns, directional R-multiple
columns, strategy-defined ranking weights, and active-only conflict groups.

## Data substitutions and deviations

- Historical bid/ask spread is unavailable, so the liquidity regime component
  never awards the spread-dependent 10-point branch and awards five points at
  the INR 50 crore ADT floor.
- Point-in-time sector membership is unavailable. Breadth uses the live top-300
  scan universe without score rescaling or fabricated constituents.
- The 25% sector exposure cap remains unavailable because the instrument
  master and evidence database contain no sector classifications.
- Mutable state serialization is replaced by deterministic state
  reconstruction from persisted completed bars; trading state, orders, and
  positions continue through the existing portfolio recovery path.
