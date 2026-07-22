# STRAT-05 — 5-minute Gap Fill Failure Reversal

_Implemented 2026-07-23 from `Intraday_Strategy_Design_Specification_Part1.docx`._

## Canonical implementation

`gap_fill_failure_5m` is the sole registered STRAT-05 implementation. No
equivalent production strategy existed, so it was implemented from scratch on
the existing opening-session and execution architecture.

The strategy reconstructs the morning pullback pivot from completed bars. It
requires a 0.8%–3.0% opening gap, a 25%–75% penetration into that gap, a pivot
that retains at least the final 10% above/below the prior close, a directional
reversal close beyond the prior candle, same-slot RVOL 1.8 long or 2.2 short,
VWAP alignment, ₹50 crore ADT20, and a 09:25–11:00 close time. Available
optional filters require the pivot to test within 0.15% of its contemporaneous
VWAP and the completed NIFTY 15-minute trend to align with the original gap.

The exact raw ranking is `0.50 × RVOL + 0.50 × (1 / penetration)`. Sizing is
fixed at the specified risk rather than confidence-graded, and only the first
qualifying failure reversal per session is emitted.

## Execution and interaction

- Directional collared limit entry with a 0.10% collar.
- Initial stop beyond the pullback pivot by 0.15 ATR.
- 1.5R exits 50%, moves the stop to breakeven, and enables a post-partial
  2 ATR chandelier.
- A complete gap fill through the prior close, eight bars below 0.4R progress,
  or the 15:15 square-off exits the remainder.
- Risk is 1% of equity and capital allocation is capped at 20%.
- STRAT-05 intentionally has no Gap & Go session blocker. It may enter after a
  stopped STRAT-04 position, exactly as required by the specification.

All state—opening gap, cumulative pivot, pivot VWAP, and stop—is reconstructed
causally from completed bars, so restart recovery needs no additional mutable
strategy-state file.

## Reuse and deviations

STRAT-05 reuses ATR, session VWAP, same-slot RVOL, prior-session liquidity,
completed NIFTY trend, collared confirmed-fill execution, directional exits,
portfolio sizing, and recovery persistence. No new shared infrastructure was
needed.

- Sector-relative strength cannot be reproduced without reliable sector
  membership and sector-index histories; the optional input is omitted rather
  than fabricated.
- The requested 25% sector cap remains unavailable for the same reason.
- Historical candles do not contain bid/ask spread. Mandatory liquidity and
  volume gates plus live broker reconciliation remain authoritative.

## Validation

Deterministic long and short fixtures cover gap qualification, penetration,
no-full-fill geometry, reversal structure, RVOL, VWAP, NIFTY trend, ranking,
directional stop, scanner registration, full-gap rejection, and the permitted
post-STRAT-04 risk path. The clean full suite passed all 812 tests with all 14
registered strategies in the exit and scanner participation matrices.
