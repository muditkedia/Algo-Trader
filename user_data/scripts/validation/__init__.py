"""Validation tooling for the Algo Trader project.

Implements the companion tooling required by architecture/VALIDATION_RULES.md
(section 24). Every module is independent, importable on its own, and
testable without market data via ``selftest.py``:

    logging_utils.py  structured JSONL + console logging for every step
    loaders.py        Freqtrade backtest exports -> canonical trades frame
    metrics.py        shared metric battery (SS6/SS7) + holding-time (SS12)
    portfolio.py      portfolio-level battery (SS16)
    monte_carlo.py    bootstrap confidence intervals (SS14)
    sensitivity.py    +/-5/10/20% parameter grid + aggregation (SS13)
    walkforward.py    fold schedules, Mode A aggregation, cadences (SS10)
    regime.py         objective regime labelling + per-regime metrics (SS11)
    stress.py         fee/slippage/streak stress transforms (SS15)
    report.py         standardized 18-section Markdown report (SS19)
    coordinator.py    orchestration layer running modules in order
    selftest.py       no-data validation of the whole package

None of these modules import from algo_core except sensitivity.py, which
introspects the settings dataclasses to enumerate tunable parameters.
"""

__all__ = [
    "loaders", "metrics", "portfolio", "monte_carlo", "sensitivity",
    "walkforward", "regime", "stress", "report", "coordinator",
]
