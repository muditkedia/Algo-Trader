# STRAT-01 — 5-minute Opening Range Breakout

_Implemented 2026-07-22 from `Intraday_Strategy_Design_Specification_Part1.docx`._

`orb_5m` is the sole registered Opening Range Breakout strategy. The retired
`orb_15m` identifier survives only in frozen research reports and the legacy
fidelity specification; those results do not describe this implementation.

## Trading contract

- 5-minute opening range; buffered breaks at 0.1% beyond the range.
- Long and short entries from 09:20 through 14:45 IST.
- Same-time-slot relative volume over 10 completed sessions: at least 2.0 for
  long and 2.5 for short.
- VWAP/EMA structure, NIFTY50 VWAP direction, 15-minute NIFTY trend, opening
  range width of 0.5–2.5 ATR, regime score at least 42/70, and confidence at
  least 55/100.
- Dynamic top-300 liquidity universe plus a strategy-local completed-session
  NATR eligibility floor of 1%.
- A 0.1% limit collar around the breakout trigger. A position is created only
  after broker-confirmed fill; unfilled orders expire on the next completed
  bar or the entry cutoff.
- Initial stop at the opening-range midpoint, capped at 1.5 ATR from entry.
  Exit 50% at 1.5R, move the remainder to breakeven, then trail at 2 ATR from
  the post-partial high/low watermark. Close on VWAP invalidation, six bars
  without 0.5R progress, or the session square-off.
- Risk is capped at 1% of equity and 20% capital allocation, with grade
  multipliers A/B/C = 1.0/0.75/0.5 and all central account safeguards retained.
- Only one opening-breakout-family allocation may execute per symbol/session.

All volume, ATR, turnover, gap, and daily eligibility calculations are causal.
Session statistics use completed sessions, and slot-relative volume shifts the
current session out before rolling.

## Reused and extended infrastructure

The implementation reuses the shared ATR, ADX, EMA, VWAP, opening-range,
crossing, cost, portfolio, and execution components. Shared additions are
limited to primitives STRAT-01 requires: rate of change, MACD histogram,
same-slot relative volume, bidirectional signals/execution, limit-collar entry
intent with fill reconciliation, strategy-owned invalidation/no-progress
exits, and post-partial chandelier state.

## Data substitutions and deviations

- Historical bid/ask spread is unavailable. The liquidity regime component
  awards the conservative 5-point ADT branch when ADT20 is at least INR 50
  crore and never awards the spread-dependent 10-point branch.
- Point-in-time NIFTY50 membership is unavailable. Breadth uses the live
  top-300 scan universe; the score is not rescaled and missing inputs are not
  fabricated.
- The specification's 25% sector-exposure cap cannot be enforced because the
  SmartAPI instrument master and the available evidence database contain no
  sector classification. Existing portfolio and capital caps remain active.
  Sector metadata must be added before this one portfolio rule can be enabled.

The mandatory entry, direction, stop, target, trailing, invalidation,
no-progress, timing, risk, and one-per-session rules are implemented.
