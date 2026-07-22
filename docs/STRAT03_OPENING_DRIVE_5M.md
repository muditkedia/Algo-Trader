# STRAT-03 — 5-minute Opening Drive Momentum

_Implemented 2026-07-23 from `Intraday_Strategy_Design_Specification_Part1.docx`._

## Canonical implementation

`opening_drive_5m` is the sole registered opening-drive strategy. It is a new,
bidirectional implementation; no equivalent strategy existed in the production
library. Historical research artifacts remain legacy evidence and are not
attributed to this implementation.

The default decision is made at the close of the first 5-minute candle
(09:20 IST). `drive_bar_index=2` moves the configured decision to 09:25. A
candidate must have a directional body covering at least 70% of its range, no
more than a 15% counter-wick, same-slot 10-session RVOL of at least 2.5, a
0.75–2.5 ATR range, a close on the correct side of session VWAP, at least
₹50 crore ADT20, NIFTY direction alignment, and an opening gap aligned with
the trade. The raw ranking score is exactly `0.60 × RVOL + 0.40 × body/range`.
STRAT-03 uses fixed full risk rather than A/B/C grading.

## Execution and lifecycle

- Directional collared limit entry at the signal close, with a 0.10% collar.
- Initial stop beyond the drive candle by 0.10 ATR.
- 1.5R first target exits 50%; the stop then moves to breakeven.
- The remainder uses a post-partial 2 ATR chandelier.
- An adverse close through VWAP, five bars without 0.5R progress, or the
  15:15 square-off closes the remaining position.
- Position risk is 1% of portfolio value and capital allocation is capped at
  20%.
- An accepted opening-drive entry blocks STRAT-01 and STRAT-02 on that symbol
  for the rest of the session, including after the drive position closes.

Drive geometry and the drive high/low are derived causally from completed bars,
so restart recovery does not depend on an additional mutable strategy-state
file. The session blocker is persisted on orders, positions, and closed trades.

## Reuse and shared changes

STRAT-03 reuses ATR, session VWAP, same-slot RVOL, prior-session liquidity,
NIFTY context, confirmed-fill execution, directional exits, and portfolio/risk
controls introduced by STRAT-01 and STRAT-02. Its implementation added a
general persisted session-block group, plus strategy hooks for exact ranking
and fixed grade sizing. These hooks retain backward-compatible defaults for
the existing library.

## Documented deviations

- Sector-relative performance cannot be reproduced because the current market
  state has no reliable symbol-to-sector/index series. The input is optional;
  NIFTY direction and aligned-gap filters are applied and no sector value is
  fabricated.
- The requested 25% sector exposure cap cannot be enforced without reliable
  sector metadata. Existing account, symbol, strategy, and capital caps remain
  active.
- Spread is not available in historical candles. The strategy retains the
  specification's mandatory liquidity gate and the shared execution collar;
  live broker fill/reconciliation remains authoritative.

## Validation

Deterministic long and short fixtures cover drive timing, candle geometry,
RVOL, ranking, fixed-risk grading, counter-wick rejection, scanner discovery,
and persistence-aware session suppression. The shared exit matrix exercises
stop, partial, breakeven, chandelier, invalidation, stagnation, and square-off
semantics, and the complete repository suite is run before commit.
The final clean run passed all 801 tests with all 13 registered strategies in
the exit and scanner participation matrices.
