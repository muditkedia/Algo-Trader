# Phase 2: STRAT-11 to STRAT-13 and Top 500 Universe

_Implemented 2026-07-23. Canonical specification: `STRAT-11-13.docx`._

## Scope and operational boundary

This was a code-only implementation. No historical candle acquisition,
backfill, cache mutation, market-data artifact generation, or operational-data
commit was performed. Validation used existing read-only frames, temporary
test stores, and deterministic synthetic QUOTE packets.

The production registry now contains 20 enabled intraday strategies: 13 on
5-minute bars, six on 15-minute bars, and one on 1-hour bars. The dynamic
universe now selects the Top 500 by the existing methodology; its candidate
pool, quality filters, liquidity metric, ordering, refresh lifecycle,
persistence format, and scanner integration are unchanged.

## Strategy implementation report

### STRAT-11 - Donchian Volatility Expansion

`donchian_volatility_expansion_5m` is a new bidirectional implementation. It
uses a session-bounded, prior-bar-only 20-bar Donchian channel; 0.10% breakout
buffer; ATR volatility-expansion ratio; channel-width/ATR constraint; EMA9/20,
VWAP, close-strength, asymmetric RVOL, liquidity, NIFTY Donchian alignment,
and time-window gates. It owns its midpoint/1.25 ATR-capped stop, 1.5R 50%
partial, breakeven and 2 ATR chandelier management, midpoint invalidation,
stagnation exit, and 15:15 square-off. Its specified priority score is
0.5 VER + 0.5 RVOL. Before TP1 it blocks ORB on the symbol, and a simultaneous
DVE signal has priority over ORB.

Reused: shared ATR, EMA, session VWAP, same-slot RVOL, prior-session metrics,
NIFTY context, execution declarations, scanner ranking, persisted suppression,
risk, portfolio, recovery, and order paths. New reusable infrastructure:
causal session-aware Donchian channels and NIFTY Donchian context.

Deviation: the optional sector-momentum entry filter is omitted because the
production feed has no causal sector-index series. Current instrument industry
metadata is used only for the mandatory 25% sector portfolio cap; it is not
fabricated into a sector return series.

### STRAT-12 - Swing Structure Trend Continuation

`swing_structure_trend_5m` is a new bidirectional, causal break/retest state
machine. It confirms k=2 fractals only after two following bars, derives HH/HL
or LH/LL structure, requires the buffered prior-swing break, shallow retest
within ten bars, directional resumption through the prior bar, EMA/VWAP,
asymmetric RVOL, liquidity, pullback-volume, completed-15m structure, NIFTY
structure, and time-window gates. It owns the structural/level stop, 1.5R 50%
partial, breakeven and 2 ATR chandelier, level-plus-VWAP invalidation,
eight-bar stagnation exit, and square-off. Its score is 0.5 RVOL +
0.5 impulse/ATR. Active ORB-retest ownership blocks SSTC, and ORB-retest wins
a simultaneous same-level candidate.

Reused: all common 5-minute features, context, scanner, execution, risk,
portfolio, persistence and recovery paths. New reusable infrastructure:
exact-lag confirmed fractal pivots, swing-structure bias, and causal completed
15-minute structure alignment.

Deviation: none among mandatory inputs. The optional NIFTY and 15-minute
confirmations and pullback-volume contraction are implemented from locally
maintained completed candles.

### STRAT-13 - Volatility Contraction Pattern

`volatility_contraction_5m` is a new bidirectional causal pattern state
machine. It requires at least two confirmed contraction waves, D1 <= 4%,
D2/D1 <= 0.60, handle-volume ratio <= 0.50, buffered pivot break, EMA/VWAP,
asymmetric RVOL, breakout-body quality, liquidity, NIFTY 10-bar Donchian
alignment, and the entry window. Three-wave contractions are retained when
present. It owns the handle-pivot/1.25 ATR-capped stop, 1.5R 50% partial,
breakeven and 2 ATR chandelier, pivot-plus-VWAP invalidation, stagnation exit,
and square-off. Its score is 0.5 RVOL + 0.5 inverse HVR. VCP has simultaneous
priority over DVE.

Reused: confirmed pivots, Donchian context, shared features, ranking,
suppression, execution, risk, portfolio, persistence and recovery.

Deviation: the optional sector-relative-strength entry filter is omitted for
the same sector-index data limitation. The specification's directionally
ambiguous textual stop-cap expression is implemented as the engine's
protective `column_atr_cap`: the structural handle stop is retained unless it
would risk more than 1.25 ATR.

## Architecture update

- The orchestrator computes causal common 5-minute features and market context
  once per symbol per completed-candle scan. Strategy `prepare` remains usable
  standalone and reuses those columns when already present.
- Shared indicators now include session-aware prior-only Donchian channels,
  exact-lag confirmed fractals, and swing-structure bias. No future bar is read
  at a decision timestamp.
- Suppression metadata now supports explicit simultaneous priority without
  embedding strategy names in scanner logic.
- Current Industry metadata is carried with the persisted universe report into
  market state. Risk sizing and admission count both open position notional
  and working entry-order notional against the 25% sector cap. Missing sector
  metadata preserves prior behavior rather than inventing a classification.
- The dashboard strategy labels and entry-rule descriptions cover all three
  additions. Scanner discovery remains automatic; no second registry exists.
- No ranking algorithm changed. Top 500 is solely a selection-size increase
  from 300 to 500, and stale cached reports built for a different size are no
  longer accepted as satisfying the active specification.

## Performance investigation report

### A. Architecture verification

Production uses `SmartApiQuoteFeed -> LocalCandleEngine ->
StreamingCandleSource -> MarketDataService -> MarketState -> Orchestrator`.
Routine scan cycles evaluate locally finalized candles built from SmartAPI
QUOTE messages. They do not request historical candles. Broker history is
limited to startup seeding, explicit gap repair/validation, and separate
research/acquisition paths. The covered streaming, local-candle, scheduler and
market-service tests passed (61 tests).

The architecture itself was correct. The identified deviation was compute,
not data flow: each 5-minute strategy independently regenerated common
features and cross-sectional context, and the ORB-retest scalar state machine
added avoidable Python overhead. Those paths were consolidated or vectorized
without changing the QUOTE/candle architecture.

### B. Scan-pipeline profile

Measurements use the existing 98-symbol warm 5-minute set and the original ten
5-minute strategies unless noted. No fetch or backfill was allowed.

| Stage | Measurement | Observation |
|---|---:|---|
| QUOTE normalize + local candle construction | 0.1528 s / 6,000 packets | 25.47 microseconds/packet |
| Candle finalization (all configured timeframes) | 0.0010 s / 500 symbols | negligible |
| Materialize 500 local 5m frames | 0.1849 s | 4.05 MiB peak |
| Existing 5m scanner, 98 symbols / 10 strategies | 118.318 s | 1,207 ms/warm symbol |
| Strategy `prepare` | 30.619 s / profiled 20-symbol slice | 68.3% of slice |
| Shared/context generation | 11.598 s / slice | 25.9% of slice |
| Entry evaluation | 2.101 s / slice | 4.7% of slice |
| Diagnostics | 0.254 s / slice | 0.6% of slice |
| Ranking | 0.0252 s / 10,000 candidates | negligible |
| Suppression/sort | 0.0086 s / 10,000 candidates | negligible |
| Portfolio + risk filters | 0.1169 s / 10,000 candidates | negligible |
| Execution-signal preparation | 1.4169 s / 10,000 candidates | low |
| Dashboard serialization | 0.0010 s / 12 files, 235 KB | negligible |
| Logging | 0.0586 s / 10,000 records | negligible |

The reported approximately 118 seconds is therefore strategy feature/context
CPU time over warm frames, not SmartAPI, QUOTE ingestion, ranking, risk,
execution, dashboard, serialization, or logging latency.

### C. Bottleneck analysis

The dominant bottleneck was repeated DataFrame work across strategies:
ATR/EMA/VWAP/same-slot RVOL/prior-session features and identical NIFTY/breadth
context were recomputed for each strategy. ORB-retest also spent material time
in row-wise scalar state tracking. After adding STRAT-12/13, the first fractal
implementation became the next measured hotspot; exact pivot confirmation and
structure traversal were then moved to causal NumPy windows/buffers.

### D. Optimizations and measured result

| Optimization | Rationale | Measured result | Behavioral impact |
|---|---|---:|---|
| Per-scan shared 5m feature/context block | remove duplicated calculations | original ten strategies: 118.318 -> 46.427 s (60.8% faster) | none; characterization tests pass |
| Array-backed ORB-retest state tracking | measured row-loop hotspot | included above | none; state-transition tests pass |
| Array-backed exact-lag fractals/structure | measured STRAT-12/13 hotspot | all 13 5m strategies: 74.202 -> 56.059 s on 98 warm symbols | none; lag/prefix-invariance tests pass |
| Pre-feature minimum-history gate | avoid enriching symbols no strategy can evaluate | current 402 non-warm symbols bypassed; no current signal-eligible work removed | none; dedicated regression test |
| Orchestrator-owned frame replacement | avoid fragmented context mutation while retaining standalone in-place API | retained the fast shared-context path | none; standalone ORB characterization passes |

The initial controlled final sample completed in 56.059 seconds. A release
recheck after clearing two benchmark processes left alive by command timeouts
completed in 86.502 seconds for the full Top 500 subscription, its 98 eligible
warm symbols, and all 13 five-minute strategies. The latter conservative
sample is used for capacity estimates. Shared results are scoped to one
completed-candle evaluation; there is no global or stale-candle cache.

### E. Scalability assessment

The conservative release rate is about 883 ms per warm symbol for all 13
five-minute strategies. Linear CPU estimates are approximately 265 seconds for
300 fully warm symbols and 441 seconds for 500 fully warm symbols. For the previous ten
strategies, the optimized estimate is approximately 142 seconds at 300 and
237 seconds at 500. These are conservative single-process, 1,600-bar production-window
figures; current production's 98 warm 5-minute symbols complete in 86.502
seconds, while the remaining
Top 500 members naturally remain ineligible until normal operational candle
state is available. This phase deliberately did not backfill them.

QUOTE ingestion and local candle memory scale comfortably: the 500-symbol
synthetic measurement remained under 5 MiB peak for construction/materialized
frames. A diagnostic allocation-traced full scan peaked at approximately
132 MiB; tracing materially distorted runtime and is not a production timing.
CPU-bound strategy preparation, rather than feed, serialization, or resident
candle memory, is the capacity constraint. A 500-symbol fully warm universe
would exceed one five-minute cycle on the measured single-process path, so
paper operation should monitor scan completion before all 500 names acquire
deep warm histories. Further work should be driven by production telemetry
and must preserve exact signal parity.

## Validation

Validation covers compilation; indicator causality and prefix invariance;
long/short fixtures for STRAT-11/12/13; execution declarations; cross-strategy
priority; registry/scanner participation; Top 500 selection; sector risk over
positions and working orders; dashboard; paper execution; persistence;
recovery; and the complete regression suite. The final release result is
895 tests passed in 108.09 seconds after clean Python compilation. All 20
strategies participated in scanning and passed the exit-engine matrix. The
Top 500 default and actual 500-symbol read-only selection were verified, along
with dashboard export, paper-engine execution, risk, persistence, and recovery.
