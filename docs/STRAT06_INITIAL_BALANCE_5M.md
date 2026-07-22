# STRAT-06 — 5-minute Initial Balance Breakout

_Implemented 2026-07-23 from `Intraday_Strategy_Design_Specification_Part1.docx`._

## Canonical implementation

`initial_balance_5m` is the sole registered STRAT-06 implementation. No
equivalent existed: STRAT-01’s 5-minute opening range is intentionally not used
as an alias for the immutable six-candle, 30-minute Initial Balance.

At the close of the sixth 5-minute candle, the strategy locks `IBhigh`, `IBlow`,
midpoint, and width for the remainder of the session. A candidate between
09:45 and 14:45 must close beyond the relevant boundary plus a 0.10% buffer,
have an IB width between 0.60 and 2.20 current ATR, meet same-slot RVOL 2.0
long or 2.5 short, close on the correct side of VWAP, align EMA9/EMA20, and
meet ₹50 crore ADT20. Available optional filters require NIFTY to trade beyond
its own locked 30-minute balance and the stock to clear the prior-session CPR.
Only the first qualifying break per session is emitted.

The exact raw ranking is `0.50 × RVOL + 0.50 × (ATR / IB width)`, with fixed
specification sizing rather than confidence grades.

## Execution and interaction

- Directional collared limit entry with a 0.10% collar.
- Initial stop at the IB midpoint, capped to a maximum 1.50 ATR distance.
- 1.5R exits 50%, moves the stop to breakeven, and enables a 2 ATR chandelier.
- An adverse VWAP close, six bars below 0.4R progress, or 15:15 square-off
  exits the remainder.
- Risk is 1% of equity and capital allocation is capped at 20%.
- The central symbol-level risk gate suppresses STRAT-06 while an earlier ORB
  or Opening Drive position is open. Because STRAT-06 has no session blocker,
  a stopped earlier opening trade does not prevent a later valid IB break.

IB state is reconstructed from completed bars and remains immutable through
the session, so restart recovery requires no separate mutable state file.

## Reuse and deviations

The strategy reuses `opening_range` with a 30-minute duration, ATR, EMA,
session VWAP, same-slot RVOL, causal CPR, prior-session liquidity, collared
execution, and directional trade management. Shared opening context now also
provides NIFTY’s own locked 30-minute balance.

- Sector-relative strength and the 25% sector exposure cap remain unavailable
  without reliable sector metadata and sector-index histories.
- CPR clearance and NIFTY-IB alignment are implemented because their causal
  inputs are available. No unavailable input is fabricated.
- Historical candles have no bid/ask spread; liquidity, the collar, and
  broker-confirmed reconciliation remain authoritative.

## Validation

Deterministic long and short fixtures cover IB locking, buffered breaks, width,
RVOL, VWAP, EMA, NIFTY IB, CPR, ranking, midpoint/ATR stop, scanner discovery,
wide-range rejection, and open-versus-stopped ORB interaction. The clean suite
passed all 822 tests with all 15 registered strategies in scanner and exit
matrices.
