# Algo Trader

An algorithmic trading platform for Indian equities (NSE), built around a
research-first workflow: measured edges, preregistered experiments, and a
production paper/live trading engine.

## Scope

- Market: Indian equities (NSE)
- Broker/data: Angel One SmartAPI
- Research & backtesting engine with preregistration and governance
- Scanner, execution engine, risk engine, portfolio management
- Market Data v2 (scheduler, request queue, transport-owned pacing)
- Paper and live trading with recovery, JSON exporters, and a dashboard

## Layout

- `src/algo/` - the platform (core, data, marketdata, research, risk,
  strategies, trading, paper, evidence)
- `scripts/` - CLI entry points (run_trading, backtests, research, universe)
- `tests/` - full regression suite
- `docs/` - project state, decisions, learnings, guides, and audits
- `dashboard/` - operator dashboard and its JSON snapshots
- `user_data/` - local market data store, universes, and trading records

## Getting started

```
.venv/Scripts/python -m pytest          # run the test suite
.venv/Scripts/python scripts/run_trading.py --help
```

See `docs/PROJECT_STATE.md` for current status and
`docs/OPERATOR_MANUAL.md` for live/paper operation.
