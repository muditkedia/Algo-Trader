# STRAT-10 — 5-minute Geometric Channel Continuation

_Implemented 2026-07-23 from `Intraday_Strategy_Design_Specification_Part1.docx`._

## Canonical implementation

`geometric_channel_5m` is the sole STRAT-10 implementation. No registered
strategy was equivalent: the historical Donchian references describe fixed
high/low breakouts, not regression-channel boundary continuation.

For each completed 5-minute evaluation candle, the strategy fits OLS to the
20 immediately preceding candles in the same session and projects the line
one bar forward. The evaluation candle is never included in the channel it
tests. Longs require R² at least 0.70, normalized slope at least +0.04% per
bar, a lower-envelope test no deeper than 0.40 ATR, a bullish close above the
projected median and prior high, VWAP support, same-slot RVOL 1.5, and ₹50
crore ADT20. Shorts are symmetric with slope at most −0.04% and RVOL 2.0.

All available optional confirmations are active: completed 15-minute
regression slope must align, channel width must be at least one ATR, and
NIFTY50's prior-only 20-bar slope must agree. A full 20-bar channel naturally
makes the first possible trigger the candle closing at 11:00 despite the
broader 09:35–14:45 configured window.

## Execution and interaction

- Directional limit entry with a 0.10% collar.
- Stop below/above the more protective of the channel envelope plus 0.15 ATR
  and the three-bar bounce pivot plus 0.10 ATR.
- 1.5R books 50% and moves the stop to breakeven.
- The live dynamic opposite channel boundary books another 25% of the original
  quantity; the final 25% runs under a 2 ATR chandelier.
- An adverse envelope or VWAP close, eight bars below 0.4R progress, or the
  15:15 square-off exits the remainder.
- A working/open STRAT-08 or STRAT-09 position suppresses STRAT-10 on that
  symbol. The one-way blocker survives restart and releases when the owner
  position closes.

## Reuse, shared infrastructure, and deviations

The strategy reuses ATR, VWAP, same-slot RVOL, turnover, completed 15-minute
aggregation, NIFTY context, collared execution, directional risk, persistence,
and recovery. New shared infrastructure is limited to the current strategy's
requirements: prior-only rolling OLS channel calculation; 15-minute/NIFTY
regression-slope context; dynamic target 2; a second partial that preserves a
runner; and one-way active-owner blocking. Backtest and live management share
the same multi-stage semantics.

- Reliable sector membership is unavailable, so the specified 25% sector
  equity cap cannot be reproduced. Existing metadata-aware sector controls
  remain active; no sector values are fabricated.
- For short candidates the priority uses `|β1|`. The source formula prints
  signed `β1`, but its prose explicitly prioritizes the steepest slope; using
  the signed value would rank stronger short trends lower.
- The worked short example says `160.20 < 160.00`; the implementation follows
  the mandatory median-cross inequality and does not reproduce that arithmetic
  error.
- Historical candles have no order-book spread; the liquidity gate, collar,
  and broker-confirmed fill lifecycle remain authoritative.
- The portfolio-wide five-position cap already matches the specification.

## Validation

Deterministic long and short fixtures cover projected OLS geometry, R², slope,
touch/breach, median/prior-bar reclaim, VWAP, asymmetric RVOL, liquidity,
15-minute/NIFTY alignment, ranking, stops, and scanner discovery. Tests prove
the channel is prior-only and reject nonlinear geometry. Backtest and live
tests verify 50%/25% partials, dynamic target 2, the 25% runner, persistence,
and active-owner risk release. Python compilation and the clean complete suite
passed: 854 tests, all 17 strategies scanning and passing the exit matrix.
