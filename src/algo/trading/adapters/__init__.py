"""Execution adapters - the ONE interchangeable component.

``ExecutionAdapter`` is the contract every stage above it depends on; the two
implementations (``PaperBroker``, ``AngelOneBroker``) are selected by
configuration alone. No business logic imports a concrete adapter.
"""

from algo.trading.adapters.base import BrokerError, ExecutionAdapter
from algo.trading.adapters.paper import PaperBroker

__all__ = ["ExecutionAdapter", "BrokerError", "PaperBroker", "build_adapter"]


def build_adapter(config, clock=None, session=None, instruments=None):
    """Construct the adapter named by ``config.mode``. The live adapter is
    imported lazily so paper mode never needs the SDK, and it enforces its own
    three-key arming (see AngelOneBroker)."""
    if config.mode == "paper":
        return PaperBroker(config, clock=clock)
    if config.mode == "live":
        from algo.trading.adapters.angelone import AngelOneBroker
        return AngelOneBroker(config, session=session, instruments=instruments)
    raise ValueError(f"unknown trading mode {config.mode!r}")
