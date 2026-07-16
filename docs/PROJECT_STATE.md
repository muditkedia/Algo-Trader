# Project State

_Last updated: 2026-07-16_

## Current Phase

**Equities pivot — Phase 4 (production strategy library) COMPLETE, uncommitted.**
Phase 1 (`4f4d630`), Phase 2 (`5a14fd6`), Phase 3 (`aa212c1`) committed & pushed.

Infrastructure building has stopped. Phase 4 implemented the six researched
Indian-equity strategies on the existing platform — every one a plug-in on the
Phase-1 `StrategyProfile` interface, scanned by the Phase-2/3 data layer. Still
**no brokers, no order placement, no paper/live trading** — by design.

## Phase 4 delivered

**`src/algo/strategies/library/`** — six long-only strategies, each with
indicator preparation, vectorized edge-triggered signal, frozen configurable
params, component-based confidence, declared regimes, and hypothesis /
expected-behaviour / failure-mode metadata:

| Strategy | Name | TF | Setup |
|---|---|---|---|
| 15m Pullback Continuation | `pullback_15m` | 15m | uptrend dip to fast EMA, reclaim bar |
| 1h Volatility Expansion | `volexp_1h` | 1h | multi-bar BB squeeze, close through upper band |
| Opening Range Breakout | `orb_15m` | 15m | break of opening-range high, volume-confirmed (mandatory) |
| VWAP Trend Continuation | `vwap_15m` | 15m | VWAP reclaim on buyer-controlled session |
| NR7 Contraction Breakout | `nr7_daily` | 1d | break of NR7 day's high on volume |
| 200 EMA Pullback | `ema200_daily` | 1d | resumption after pullback into rising anchor zone |

**Supporting (reuse-first):**
- `core/indicators.py` — pure-pandas library: `crossed_above/below` verbatim
  from the archived crypto core; Wilder RSI/ATR/ADX (same formulas as the
  validation regime labeler); Bollinger; session VWAP; opening range (D-017:
  talib dropped — C dependency, pandas equivalents already proven in-repo).
- `strategies/confidence.py` — Component/weighted mechanism promoted from the
  archived DecisionEngine conviction score. **Confidence scores are heuristic
  signal-quality hypotheses**: every component is recorded to evidence so the
  research engine can later measure which discriminate and recalibrate (L-003
  lesson institutionalized).
- `strategies/base.py` — `StrategyMeta` gains `timeframe`/`min_bars`;
  `StrategyProfile` gains `prepare()`, `confidence()`, `min_history()`.
- `scanner/engine.py` — **multi-timeframe unified scan**: strategies grouped by
  their declared timeframe, bars loaded once per (symbol, timeframe),
  per-strategy prep + signal + confidence, ONE ranked opportunity list. Every
  firing candidate recorded to evidence with its component breakdown;
  **duplicate-signal protection** via `EvidenceLogger.signal_exists` (re-scans
  are idempotent).
- Fixed a latent Phase-2 bug: `MarketDataStore.read` failed on tz-aware bounds.

## Validation performed

- `pytest`: **113 passed** (77 prior + 36 new). Per strategy: crafted
  deterministic positive frames (fires exactly on the setup bar), negatives
  (no volume → no ORB; downtrend → no pullback; wide day → no NR7; seller
  session → no VWAP; no squeeze → no volexp; short history → silent ema200),
  flat-market zero-signal sweep, unprepared-frame guard (never raises),
  metadata contract, unique names, registry discovery of all six.
- Unified scan: one ranked list across 15m/1h/1d, ranks 1..N by confidence,
  every opportunity written to evidence with components, re-scan adds zero
  duplicate signals.
- End-to-end smoke: 23 symbols × 6 strategies, 3 timeframes → ranked list in
  ~0.8 s; dedup verified on disk.

## Strategy lifecycle position (frozen architecture)

All six are **CANDIDATES** (evidence status `draft`): scannable, every signal
recorded, but none has passed the D-007 measure gate (edge ≥ 2× cost with CI
support). The next phase runs them through the research engine — expect some to
be honestly rejected; that is the system working.

## Environment

Python 3.11 venv, editable install; pandas 3.0.3 / numpy 2.4.6 / pyarrow 25.
No talib, no new dependencies.

## Next (Phase 5 — pending owner approval)

Research-engine port: promote the edge lab (measure_entry_edge/entry_edge_lab)
from the archive, outcome labeler, trade simulator (promote archived
risk_engine + trade_manager with session square-off), equity-parameterized
validation battery → measure all six strategies against the cost hurdle and
produce the first league table.

## Open decisions / actions needed from owner

- Approve committing Phase 4.
- Historical data for measurement: intraday strategies need 15m/1h history —
  CSV export import (available now) or wait for Kotak daily accumulation
  (daily strategies only, slow). CSV import recommended for Phase 5.
- Authorize drafting `architecture/VALIDATION_RULES_EQ.md` (equity gates must
  be pre-registered BEFORE the first measurement run, per protocol).

## Open blockers

- None for the code. Measurement depth depends on the historical-data decision.
