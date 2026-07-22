# STRAT-07 — 5-minute Opening Liquidity Sweep & Reversal

_Implemented 2026-07-23 from `Intraday_Strategy_Design_Specification_Part1.docx`._

## Canonical implementation

`liquidity_sweep_5m` is the sole registered STRAT-07 implementation. No
equivalent strategy existed in the production library.

The strategy maintains the first 5-minute high/low and prior-session high/low,
then selects the nearest structural boundary. Between 09:20 and 10:30, a setup
must pierce that boundary by no more than 0.50 ATR, print a rejection wick of
at least 40%, close back inside with a directional body, meet same-slot RVOL
2.0 and ₹50 crore ADT20, and be the first qualifying sweep of the session.

Available optional filters are active: RSI(14) must be below 30 for a bullish
spring or above 70 for a bearish upthrust; NIFTY must not confirm the stock’s
opening-range sweep; and the swept level must be within 0.15% of CPR or the
prior-day extreme. The exact raw ranking is
`0.50 × wick ratio + 0.50 × RVOL`, with fixed specification sizing.

## Execution and interaction

- Directional collared limit entry with a 0.10% collar.
- Initial stop beyond the sweep wick by 0.10 ATR.
- TP1 exits 50% at VWAP or 1.5R, whichever is reached first, then moves the
  stop to breakeven.
- The remainder targets the opposite opening-range boundary and is protected
  by a post-partial 1.75 ATR chandelier.
- If VWAP has not been touched within six completed bars, the remainder exits;
  all positions square off at 15:15.
- Risk is 1% of equity and capital allocation is capped at 20%.
- A working or filled sweep suppresses STRAT-01 and STRAT-06 on that symbol for
  exactly 60 minutes. The persisted expiry survives position close and restart.

The source text says to liquidate the remaining 50% at TP2 and then describes
a runner beyond TP2, which is arithmetically impossible. The deterministic
implementation honors full liquidation at TP2; the chandelier protects the
post-TP1 remainder while it travels toward TP2. No nonexistent shares are
fabricated.

## Reusable infrastructure and deviations

STRAT-07 reuses opening ranges, prior-session extremes, ATR, RSI, VWAP,
same-slot RVOL, CPR, NIFTY context, collared fills, and directional management.
It introduced only capabilities the strategy requires:

- directional second-target columns;
- a persisted directional level-specific timeout shared by backtest and live
  management;
- persisted time-bounded strategy suppression through signals, orders,
  positions, closed records, recovery, and the risk gate;
- exact NIFTY 5-minute high/low and opening-range context.

Sector metadata remains unavailable, so the 25% sector cap is documented but
cannot be enforced. No sector value is fabricated.

## Validation

Deterministic long and short fixtures cover boundary selection, depth, wick,
reclaim, RVOL, RSI, NIFTY non-confirmation, confluence, ranking, directional
targets, VWAP timeout, scanner discovery, deep-sweep rejection, and persisted
60-minute suppression across restart. The final clean complete suite passed
all 833 tests with all 16 registered strategies in scanner and exit matrices.
