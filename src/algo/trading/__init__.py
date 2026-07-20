"""The production trading system - ONE codebase for paper and live.

Pipeline (each module has a single responsibility):

    MarketDataService -> MarketState -> Orchestrator (scan + strategy eval)
      -> AccountRiskEngine -> PortfolioEngine -> OrderManager
      -> ExecutionAdapter (PaperBroker | AngelOneBroker)
      -> TradeLogger/EventLog -> Health/Dashboard

Market data is a subsystem of its own (`algo.marketdata`): the service is the
ONE fetcher, and every stage above reads `MarketState` and nothing else. See
`docs/MARKET_DATA_ARCHITECTURE_V2.md`.

Paper and live modes are IDENTICAL except for the execution adapter, chosen
by configuration (`TradingConfig.mode`). The live adapter additionally
requires an explicit multi-key arming sequence (config flag + environment
variable) and remains disabled by default.

Strategies, their ExecutionSpecs, and the verified backtest engine are
consumed as frozen inputs - the live TradeManager interprets each position's
owning ExecutionSpec with the SAME level semantics (reusing the engine's
level helpers) and delegates fills to the adapter.
"""

from algo.trading.config import TradingConfig
from algo.trading.engine import ProductionEngine

__all__ = ["TradingConfig", "ProductionEngine"]
