# Project State

_Last updated: 2026-07-16_

## Current Phase

**Equities pivot — Phase 5 (measurement) COMPLETE, uncommitted.**
Phases 1–4 committed & pushed (`4f4d630`, `5a14fd6`, `aa212c1`, `7a1aeac`).

The Research Engine is now fully operational: the complete evidence loop —
measure entry edge → record every signal with confidence → label outcomes →
simulate managed trades → evaluate → cost sensitivity → confidence calibration
→ league table with verdicts — runs end to end. Still **no brokers, no paper,
no live trading** — by design.

## Phase 5 delivered

- **NSE cost model** (`core/costs.py`): full charge stack — brokerage(+cap),
  STT (intraday sell-side / delivery both-sides), exchange txn, SEBI, stamp
  (buy-side), GST on chargeables, slippage. **Every component configurable**
  (statutory defaults, no broker hardcoding); per-fill `breakdown()` auditable
  against a contract note. Intraday round trip ≈ 12 bps, delivery ≈ 31 bps at
  a ₹1L ticket.
- **Risk engine promoted verbatim** from the archive (`risk/engine.py`): pure
  functions unchanged; `RiskParams` drops only the D-006-removed structural-exit
  fields.
- **Trade simulator** (`research/simulator.py`): bar replay reusing
  `initial_stop_pct`/`trailing_stop_price` + the archived TradeManager's
  monotonic stop-ratchet algorithm. Exits: ATR/structure stop, trailing stop,
  **session square-off** (intraday market rule), horizon end. Canonical trade
  output; all P&L net of the cost model.
- **Edge lab** (`research/edge_lab.py`): the promoted L-009 methodology —
  forward returns, MFE/MAE vs random baseline, **day-clustered bootstrap CIs**,
  verdict on the CI lower bound vs cost (not the point estimate).
- **Outcome Labeler** (`research/labeler.py`): fills `signal_outcomes` for
  matured signals (forward returns, MFE/MAE, overnight gap, modelled cost,
  simulated exit + exit quality). Idempotent; immature signals deferred.
- **ResearchEngine completed** (`research/engine.py`): `measure_edge`,
  `label_outcomes`, `simulate_strategy`, `evaluate_strategy`,
  `cost_sensitivity` (1x/2x), `confidence_calibration` (the L-003 question:
  does confidence correlate with outcome?), `measure_all` → `StrategyVerdict`s
  → `league_table`. Verdict bars are the PRE-REGISTERED ones (D-007 2×-cost
  hurdle on the CI lower bound; §7 PF/expectancy; §15 2×-cost survival;
  ≥30-signal evidence floor) — nothing tuned to pass.
- **Measurement runner** (`scripts/run_measurement.py`): full sweep; works on
  synthetic data today and takes `--csv-root` for real data with zero code
  changes.

## Measurement results

**Machinery validation (test controls):** a planted-edge strategy → **PASS**;
pure noise → **FAIL/BORDERLINE**. The pipeline demonstrably discriminates in
both directions — it can reject, and only rejects what deserves it.

**League table on the synthetic corpus (12 symbols, ~410 daily sessions):
all six strategies FAIL** — correctly: a seeded random walk contains no
exploitable pattern, and the D-007 gate rejected every entry (CI lower bounds
+4.6 to −12 bps vs 12–31 bps costs; PFs 0.62–1.14). Confidence↔outcome
correlations ≈ 0 (−0.19..+0.12), as expected on noise.

**⚠️ These are verdicts on SYNTHETIC data — they validate the machinery, not
the strategies' market worth.** Real verdicts require real NSE history. The
run also proved the honesty property: nothing in the pipeline is biased toward
implementation (1,053 signals recorded, 1,039 outcomes labeled, zero passes).

## Validation performed

`pytest`: **141 passed** (28 new): NSE cost model vs hand-computed charges
(intraday/delivery, buy/sell asymmetry, cap binding, full configurability),
simulator exit paths (stop, trailing-after-ratchet, session square-off,
delivery multi-day, structure-vs-ATR stop, hard-stop cap, cost netting,
canonical record), edge-lab math (exact forward returns/MFE/MAE, deterministic
day-clustered CI), **positive & negative controls**, labeler exactness +
idempotency, engine persistence, cost-sensitivity degradation, verdicts +
league table, INCONCLUSIVE floor.

## Next (Phase 6 — pending owner approval)

**Real data in, real verdicts out.** Import genuine NSE history (CSV export —
15m/1h/1d for a liquid universe) and re-run
`scripts/run_measurement.py --csv-root <dir>`. Strategies that PASS on real
data proceed toward walk-forward validation and paper trading; the rest are
retired with their evidence.

## Open decisions / actions needed from owner

- Approve committing Phase 5.
- **Provide real NSE historical data** (CSV export) — the only blocker for
  real verdicts. Format: `<root>/<timeframe>/<SYMBOL>.csv` with
  date,open,high,low,close,volume.
- Optionally amend/pre-register equity-specific gate values in a
  VALIDATION_RULES_EQ before examining real OOS data (current bars are the
  crypto-era pre-registered ones, applied unchanged).

## Open blockers

- Real verdicts blocked on real data only. Machinery is complete and proven.
