# Archived: crypto / Freqtrade phase

This directory preserves the crypto phase of the project (Freqtrade, Binance
spot, BTC/ETH). It was moved here intact during the Phase-1 pivot to the Indian
equities research platform. **Nothing is deleted** - this code remains runnable
from history and is the source of the empirical lessons in `docs/LEARNINGS.md`
and `docs/DECISIONS.md`.

## Why it was archived

Freqtrade cannot trade Indian equities (its brokers come from CCXT; there is no
NSE/Zerodha/Dhan/Upstox path, no session calendar, no equity microstructure).
The strategy it hosted was also a proven negative result: the measured entry
edge (~3 bps gross/trade) never cleared the ~20 bps round-trip crypto fee
(D-007 / L-009). So the *shell* is retired; the *lessons* and the *reusable
analytical core* carry forward.

## What was reused (NOT here - already promoted)

* The **validation package** (`user_data/scripts/validation/`) was moved to
  `src/algo/research/validation/` and made market-agnostic. It is the platform's
  measurement battery.

## Phase-2 promotion candidates (still here, promote when the Trade Simulator
## and first strategies are built)

These crypto modules are market-agnostic in substance and will be *promoted out
of the archive* (adapted, not rewritten) when Phase 2/3 needs them:

| File | Promote to | Adaptation needed |
|---|---|---|
| `strategies/algo_core/indicators.py` | `algo/core/indicators.py` | none (pure ta) |
| `strategies/algo_core/risk_engine.py` | `algo/risk/engine.py` | ₹ units; gap-risk note |
| `strategies/algo_core/trade_manager.py` | `algo/manage/trade_manager.py` | session square-off exit |
| `strategies/algo_core/decision_engine.py` | `algo/confidence/engine.py` | equity liquidity/spread inputs |
| `strategies/algo_core/settings.py` | pattern already generalized in `algo/core/config.py` | — |

## Pure crypto artifacts (retained for the record, not for reuse)

* `strategies/AdaptiveTrendStrategy*.py` - the v1/v2/v3 Freqtrade strategies.
* `strategies/algo_core/{settings,decision_engine,trade_manager}_v2/v3.py`,
  `profiles/trend_following_v2.py` - the v1->v3 experiment artifacts.
* `strategies/algo_core/profiles/*` - crypto trade theses (interface generalized
  in `src/algo/strategies/base.py`).
* `scripts/*` - the Phase D/E/F research scripts (`measure_entry_edge.py` and
  `entry_edge_lab.py` are the methodological seed of the Phase-2 edge lab),
  plus `run_validation.py` / `validate_strategy.py` (Freqtrade-coupled).
* `config.json`, `config_analysis.json`, `docker-compose.yml`,
  `hyperopts/sample_hyperopt_loss.py` - Freqtrade runtime scaffolding.
