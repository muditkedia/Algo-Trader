# STRAT-09 — 5-minute EMA Compression Breakout

_Implemented 2026-07-23 from `Intraday_Strategy_Design_Specification_Part1.docx`._

## Canonical implementation

`ema_compression_5m` is the sole STRAT-09 implementation. The existing
`volexp_1h` remains registered because it is a materially different long-only
hourly Bollinger squeeze, not an EMA-compression duplicate.

The strategy is bidirectional on completed 5-minute bars. Between 09:30 and
14:45 it requires all four preceding session bars to have an EMA8/EMA20/EMA50
spread no wider than 0.35%, then requires a 0.10%-buffered ribbon break in the
EMA200 and session-VWAP direction. The trigger candle must have at least a 50%
directional body, same-slot ten-session RVOL of 2.0 long or 2.5 short, and
prior 20-session average daily turnover of at least ₹50 crore.

All reproducible optional confirmations are active: the preceding Bollinger
bandwidth must be at a ten-bar low, NIFTY50 must be on the matching side of its
5-minute EMA20, and 5-minute ADX14 must rise with matching +DI/−DI dominance.
The exact raw priority is `0.50 × RVOL + 0.50 × (1 / EMA spread)` and sizing is
the specification's fixed 1% account risk capped at 20% capital.

## Execution and interaction

- Directional limit entry with a 0.10% collar.
- The initial stop sits beyond the four-bar compression range by 0.10 ATR and
  is capped at 1.25 ATR from entry.
- 1.5R exits 50%, moves the stop to breakeven, and enables a post-partial
  2 ATR chandelier.
- An adverse EMA20 close, six bars below 0.4R progress, or the 15:15
  square-off exits the remainder.
- While the entry is working and until TP1 is booked, secondary VWAP, EMA
  pullback, Supertrend, and volatility-expansion trend entries on the same
  symbol are suppressed. The block is persisted with order/position state and
  releases immediately after the partial.

## Reuse, shared infrastructure, and deviations

The strategy reuses EMA, ATR, session VWAP, same-slot RVOL, Bollinger
bandwidth, prior-session turnover, market context, directional execution, and
the existing portfolio/risk pipeline. Shared directional-movement calculation
now exposes +DI, −DI, and ADX from one implementation; NIFTY context now
exposes EMA20. A reusable pre-TP1 blocker was added end to end through signal,
order, position, risk, persistence, and recovery.

- The stop equation in the source uses `min` for a long stop, but the prose
  says the distance is capped at 1.25 ATR and its worked example uses the
  nearer structural stop. The implementation follows the prose and example:
  `max(structural stop, entry - 1.25 ATR)` for longs, symmetric for shorts.
- Reliable sector membership is unavailable, so the specified 25% sector
  equity cap cannot be reproduced. The existing portfolio sector-count guard
  remains active when metadata exists; no sector values are fabricated.
- Historical candles have no order-book spread. The mandatory liquidity gate,
  limit collar, and broker-confirmed fill lifecycle remain authoritative.
- The portfolio-wide five-position cap already matches the specification.

## Validation

Deterministic long and short fixtures cover causal compression, buffered
ribbon release, asymmetric RVOL, candle body, EMA200/VWAP, Bollinger, NIFTY,
ADX/DI, liquidity, ranking, capped directional stop, and scanner discovery.
Negative tests cover a broken compression sequence and duplicate registration.
Portfolio tests cover working-order suppression, risk denial, TP1 release, and
restart recovery. Python compilation and the complete suite passed: 840 tests,
all 16 registered strategies scanning, and all 16 passing the exit matrix.
