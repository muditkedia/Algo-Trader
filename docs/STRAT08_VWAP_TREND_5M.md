# STRAT-08 — 5-minute VWAP Trend Continuation

_Implemented 2026-07-23 from `Intraday_Strategy_Design_Specification_Part1.docx`._

## Canonical consolidation

`vwap_trend_5m` is the sole registered VWAP continuation implementation. It
replaces both `vwap_15m` (a long-only reclaim after VWAP loss) and
`vwap_pullback_15m` (a long-only tag-and-hold bounce). Their research and
fidelity reports remain frozen legacy evidence and are not attributed to the
new implementation.

The canonical strategy is bidirectional on completed 5-minute bars. Between
09:30 and 14:45 it requires strict EMA9/EMA20/EMA50 hierarchy, a three-bar VWAP
slope of at least ±0.05%, a current-bar test within 0.15% of VWAP, penetration
no deeper than 0.40 ATR, a directional close beyond VWAP and the prior candle,
same-slot RVOL 1.5 long or 1.8 short, and ₹50 crore ADT20.

Available optional filters are active: causally completed 15-minute ADX must
be at least 22 and the reversal wick must be at least 35% of the candle range.
The exact raw ranking is
`0.50 × |three-bar VWAP slope %| + 0.50 × RVOL`, with fixed specification
sizing.

## Execution and interaction

- Directional collared limit entry with a 0.10% collar.
- Initial long stop is the lower of pullback-pivot minus 0.10 ATR and VWAP
  minus 0.25 ATR; short logic is symmetric.
- 1.5R exits 50%, moves the stop to breakeven, and enables a post-partial
  2 ATR chandelier.
- An adverse VWAP close, six bars below 0.4R progress, or the 15:15 square-off
  exits the remainder.
- Risk is 1% of equity and capital allocation is capped at 20%.
- STRAT-08 has no opening-strategy blocker and is therefore permitted as a
  secondary continuation after a completed STRAT-01 or STRAT-06 trade.

All state is reconstructed causally from completed bars, including the
three-bar VWAP slope, rolling pullback pivot, and completed 15-minute ADX.

## Reuse and deviations

The strategy reuses EMA, ATR, session VWAP, same-slot RVOL, completed 15-minute
aggregation/ADX, prior-session liquidity, collared execution, and existing
directional exit management. No new shared infrastructure was needed.

- Sector-index VWAP confluence and the 25% sector exposure cap remain
  unavailable without reliable sector membership and sector-index history.
- ADX and wick filters are implemented because their inputs are available.
- Historical candles contain no order-book spread; liquidity, the collar, and
  broker-confirmed fills remain authoritative.

## Validation

Deterministic long and short fixtures cover ribbon alignment, VWAP slope/test,
penetration, reversal geometry, asymmetric RVOL, ADX, wick, liquidity, ranking,
directional stop, scanner discovery, flat-VWAP rejection, and absence of both
legacy registrations. The clean suite passed all 829 tests with all 15
registered strategies in scanner and exit matrices.
