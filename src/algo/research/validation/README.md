# Validation tooling

Implements the companion tooling required by `architecture/VALIDATION_RULES.md`
§24. Every module is independent and testable without market data.

| Module | Protocol | Provides |
|---|---|---|
| `logging_utils.py` | §7 (task) | console + structured JSONL step logging |
| `loaders.py` | — | backtest export / rejections / config loading, canonical trade schema |
| `metrics.py` | §6, §7, §12 | shared metric battery, holding-time distribution, drawdown episodes |
| `portfolio.py` | §16 | full portfolio battery incl. worst-case simultaneous stop |
| `monte_carlo.py` | §14 | iid + stationary bootstrap, 90/95% CIs, CI gates, streak distribution |
| `sensitivity.py` | §13 | ±5/10/20% grid, backtest planning, curve/tornado/cliff aggregation |
| `walkforward.py` | §10, §10.1 | fold schedules (monthly/quarterly/semi-annual), Mode A planning + aggregation, WFE, cadence scoring, Mode B `Optimizer` hook |
| `regime.py` | §11 | objective daily trend/vol labels, trade tagging, per-regime metrics |
| `stress.py` | §15 | double-fee / slippage / streak stress; spike windows + delayed-exit interface |
| `report.py` | §19 | standardized 18-section Markdown report + §7 verdict |
| `coordinator.py` | task §6 | ordered orchestration of the data-free pipeline |
| `selftest.py` | — | validates the whole package on synthetic data |

## Usage (inside the container)

```bash
# validate the tooling itself (no market data needed):
docker compose run --rm --entrypoint python3 freqtrade \
    user_data/scripts/validation/selftest.py

# run the pipeline over a backtest export (post-smoke-test phase):
docker compose run --rm --entrypoint python3 freqtrade \
    user_data/scripts/run_validation.py \
    --trades user_data/backtest_results/smoke.json \
    --out user_data/backtest_results/reports/smoke_report.md \
    --config user_data/config.json --title "Smoke Test Report"
```

Reports land in `user_data/backtest_results/reports/` and structured logs in
`user_data/logs/validation_*.jsonl` (both git-ignored). Summary + verdict get
copied into `docs/LEARNINGS.md` per the protocol.

## Deliberate placeholders (per protocol phase)

- `stress.delayed_exits` — needs candle data; interface frozen, raises until
  the download phase.
- `walkforward.NotImplementedOptimizer` — Mode B optimization is a separate
  approved task (parameter exposure required).
- Loader JSON fallback verified against a synthetic fixture only; the real
  export format is exercised at the smoke test (no backtests run yet).
- Regime step activates when daily OHLCV is supplied to the coordinator.
