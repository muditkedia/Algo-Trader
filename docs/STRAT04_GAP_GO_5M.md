# STRAT-04 — 5-minute Gap & Go Acceleration

_Implemented 2026-07-23 from `Intraday_Strategy_Design_Specification_Part1.docx`._

## Canonical replacement

`gapgo_5m` is the sole registered Gap & Go implementation. It replaces the
retired long-only `gapgo_15m` approximation. The earlier 15-minute research
and adversarial reports remain frozen legacy evidence and are not attributed
to STRAT-04.

The replacement is bidirectional and evaluates completed 5-minute candles. A
candidate must open 1.0%–3.5% away from the previous close, retain at least
80% of that gap during the opening candle, break the opening candle in the gap
direction on a same-direction candle, meet opening-slot RVOL 2.5 (long) or 3.0
(short), close on the correct side of VWAP, remain inside the 09:20–10:30
window, and satisfy ₹50 crore ADT20. The available optional EMA9/EMA20 and
same-direction NIFTY opening-gap filters are also enforced. Only the first
qualifying acceleration per session is emitted.

The exact raw ranking is `0.50 × |Gap%| + 0.50 × opening RVOL`; sizing is fixed
at the specification risk rather than confidence-graded.

## Execution and interaction

- Directional collared limit entry with a 0.10% collar.
- Initial stop uses the wider of the opening-candle extreme and 1.25 ATR,
  following the specification formula.
- 1.5R exits 50%, moves the stop to breakeven, and enables a 2 ATR chandelier.
- An adverse VWAP close, six bars below 0.5R progress, or 15:15 square-off
  closes the remainder.
- Risk is 1% of equity and capital allocation is capped at 20%.
- A working or filled STRAT-04 entry suppresses STRAT-01 and STRAT-02 on that
  symbol for the rest of the session, including after close and restart.

## Reuse and shared changes

The strategy reuses `opening_range`, ATR, EMA, session VWAP, same-slot RVOL,
prior-session liquidity, NIFTY context, directional execution, confirmed-fill
orders, and the session blocker introduced by STRAT-03. Opening context now
also exposes the causal NIFTY opening-gap percentage; this is reusable by
subsequent opening-gap strategies.

## Documented deviations

- No reliable pre-market news or earnings-calendar feed is connected, so the
  optional catalyst tag is omitted rather than fabricated.
- The requested 25% sector cap remains unavailable without reliable sector
  metadata. Existing account, symbol, strategy, risk, and allocation limits
  remain active.
- Historical candles have no bid/ask spread. The mandatory ADT20/RVOL gates,
  the limit collar, and broker-confirmed fills remain authoritative.

## Validation

Deterministic long and short fixtures cover the complete gap geometry, RVOL,
VWAP, EMA, NIFTY-gap, ranking, directional stop, scanner registration, gap-fill
rejection, and persistence-aware ORB suppression. The shared exit matrix covers
stop-first fills, gaps through stops, partial/breakeven/chandelier management,
VWAP invalidation, stagnation, and square-off.
The final clean run passed all 802 tests with all 13 registered strategies in
the exit and scanner participation matrices.
