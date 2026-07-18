# Intraday Production Strategy Library — Batch 1 (Specification)

_Phase 15, 2026-07-18. Locked BEFORE implementation. Five production intraday
strategies, implemented as the published trading logic — NO optimisation, NO
tuning, NO AI. This is the production BASELINE._

## Architecture note (applies to all five)

Per the frozen platform design (D-006, VALIDATION_RULES): a `StrategyProfile`
defines only the ENTRY; exits are handled UNIFORMLY by the risk engine, which the
crypto phase proved is the value-preserving choice (per-strategy structural exits
had a 0% win rate). So for every strategy below:

- **Stop loss:** the risk engine's initial stop — the wider of 2.0×ATR and the
  structure (swing-low) distance, capped at the 6% hard stop. (`RiskParams`.)
- **Trailing:** the promoted ATR chandelier trail (2.0×ATR) once in profit, plus
  the monotonic profit-lock ladder. Never widens.
- **Target:** NONE by design (D-006 — no ROI table, no fixed target). Exits are
  stop → trailing → square-off only. A fixed "published" target is recorded for
  reference where the source specifies one, but the production exit is the uniform
  model above; adding per-strategy targets would require modifying the frozen
  risk engine.
- **Square-off:** intraday (MIS) product — the engine force-closes at the
  session's last bar (15:15 IST square-off in production; the backtest closes at
  the last available intraday bar of the session).
- **Trading session / timeframe:** NSE 09:15–15:30 IST, **15-minute** bars.
- **No-trade conditions:** before the opening range has formed (first
  `range_minutes`), after square-off, and when required indicators are not warmed
  up. Long-only.
- **Costs (backtest):** the full `NseEquityCostModel` INTRADAY stack (brokerage +
  STT sell-side + exchange + GST + stamp + SEBI + slippage), ~12.2 bps round trip.

---

## 1. Opening Range Breakout (ORB) — `orb_15m` · EXISTS (Phase 4), reused unchanged
- **Rationale:** the first 15 minutes establish the day's initial auction range;
  a volume-confirmed break of its high marks initiative buying that persists.
- **Entry:** after the opening range, `close` crosses above the opening-range
  high AND breakout-bar volume ≥ 1.5× its 20-bar average (volume MANDATORY).
- **No-trade:** during the opening-range window; without volume confirmation.
- **Published stop/target (reference):** stop below the opening-range low; target
  1× the range. **Production exit:** uniform (above).
- _Implemented as `library/orb_15m.py`; reused as-is (no duplication)._

## 2. VWAP Pullback — `vwap_pullback_15m` · NEW
- **Rationale:** on a session trending up along a RISING VWAP, price that pulls
  back to TAG VWAP as dynamic support and bounces WITHOUT losing it is buyers
  defending the institutional benchmark — a continuation entry at a discount.
- **Distinct from `vwap_15m` (VWAP Trend Continuation):** vwap_15m buys the
  RECLAIM after price closes BELOW VWAP and crosses back above. VWAP Pullback buys
  the BOUNCE off VWAP support where price's LOW tags VWAP but the CLOSE never
  loses it — a shallower "pullback to support", a different mechanism.
- **Entry:** `close > vwap` AND VWAP rising AND a recent bar's `low` tagged VWAP
  (within a small band, close held above) AND the current bar resumes (closes
  above the prior bar's high). Edge-triggered on the resume.
- **No-trade:** VWAP falling or price below VWAP; first `warmup` bars.
- **Stop/target:** uniform. **Session/timeframe/square-off:** shared.
- **Assumptions:** session VWAP resets daily (it does — `session_vwap`); VWAP is a
  meaningful support only in a genuine uptrend (the rising + above filters).

## 3. VWAP Trend Continuation — `vwap_15m` · EXISTS (Phase 4), reused unchanged
- **Rationale:** on a buyer-controlled session (price mostly above VWAP), a dip
  THROUGH VWAP that is immediately reclaimed is absorption, not distribution.
- **Entry:** ≥60% of prior session bars closed above VWAP AND (after warmup) the
  bar closes back above VWAP after trading below it.
- _Implemented as `library/vwap_15m.py`; reused as-is._

## 4. CPR Breakout — `cpr_breakout_15m` · NEW
- **Rationale:** the Central Pivot Range (from the PRIOR day's H/L/C) marks the
  day's expected value area. A narrow CPR that price breaks ABOVE (through the top
  central level) signals a trend-up day — the widely-used CPR breakout.
- **Entry:** after the opening range, `close` crosses above the CPR TOP (from the
  prior session), with breakout-bar volume ≥ 1.5× its 20-bar average.
- **CPR math (prior session H/L/C):** Pivot `P=(H+L+C)/3`, BC `=(H+L)/2`,
  TC `=2P−BC`; top/bottom = max/min(TC,BC). All from the COMPLETED prior session
  (causal). A "narrow CPR" (small TC−BC vs ATR) is the higher-conviction trend
  setup (confidence only, not a gate).
- **No-trade:** during the opening-range window; no prior session (first day);
  without volume.
- **Stop/target:** uniform. **Published stop (reference):** below the CPR
  bottom / pivot.
- **Assumptions:** the prior session's OHLC is complete and known at the current
  open (causal); one session per calendar day.

## 5. First Pullback After Breakout — `first_pullback_15m` · NEW
- **Rationale:** the highest-quality continuation entry is the FIRST pullback
  after an intraday breakout — the initial breakout draws in momentum, the first
  dip that HOLDS the breakout level (higher low) confirms demand, and the resume
  offers a defined-risk entry. Distinct from `pullback_15m` (any EMA pullback in
  an uptrend): this requires a prior BREAKOUT and takes only the FIRST pullback.
- **Entry:** (a) a breakout occurred this session (close crossed above the
  opening-range high); (b) the FIRST subsequent pullback bar that stays ABOVE the
  breakout level (a down/inside bar, low ≥ OR high — a higher low); (c) the next
  bar RESUMES (close above the pullback bar's high). One entry per breakout.
- **No-trade:** no breakout yet this session; pullbacks after the first; if a
  pullback closes back below the OR high (breakout failed).
- **Stop/target:** uniform. **Published stop (reference):** below the first
  pullback's low.
- **Assumptions:** the opening range defines the breakout level; the "first"
  pullback is the first qualifying dip after the session's breakout.

---

## Ranking plan (Part E)

All five backtested on the 15m NIFTY-100 store (99 symbols, ~3.5y) with identical
capital / brokerage / slippage / risk limits, through the risk-engine simulation.
Ranked on: strongest overall (net return × PF), most consistent (fewest negative
months / lowest month-return variance), highest expectancy, lowest max drawdown,
best risk-adjusted (Sharpe). Reported with the caveat established by D-026: NSE
intraday costs (12.2 bps round trip) are a first-order headwind, so the BASELINE
is expected to be cost-challenged — the point is a measured, comparable baseline,
not a claim of profitability.
