"""Strategy-owned execution - each strategy declares its trading behaviour.

Project-reset design change (2026-07-18): the platform no longer imposes a
common exit model on strategies. Every strategy declares an
:class:`ExecutionSpec` - entry timing, stop-loss, profit target, trailing
rules, session restrictions, holding limit and square-off - and the execution
engine (:func:`execute_signal`) is a strategy-agnostic interpreter of that
declaration. Strategies with identical internal behaviour share spec
constructors (helpers), preserving ownership without duplicating code.

The frozen research pipeline (``algo.research.simulator`` and everything that
cites its recorded verdicts) is untouched: this package is the new production
backtest path, not a rewrite of history.
"""

from algo.execution.engine import ExecutedTrade, execute_signal
from algo.execution.spec import (
    ExecutionSpec, atr_trail_intraday, atr_trail_swing, structural_intraday,
)

__all__ = [
    "ExecutedTrade", "ExecutionSpec", "atr_trail_intraday", "atr_trail_swing",
    "execute_signal", "structural_intraday",
]
