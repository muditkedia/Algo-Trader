"""Algo research platform - market-agnostic core for equity strategy research.

Sub-packages:
    core       market primitives: config pattern, logging, trading calendar,
               cost model (all market-agnostic interfaces).
    evidence   the SQLite evidence database, ORM-free models, and the logger
               that records every signal, outcome, and trade.
    strategies the plugin interface (StrategyProfile) and registry.
    universe   the composable universe-filter framework (no concrete rules).
    scanner    the scanner interface (no broker, no market data).
    research   the Research Engine and the reused validation package.

Nothing here imports a broker SDK or market data source. Phase 1 delivers the
platform foundation only - interfaces and infrastructure, no strategies.
"""

__version__ = "0.1.0"
