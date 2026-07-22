"""Shared controlled vocabularies used across sub-packages.

Kept in ``core`` so evidence, strategies, and scanner all depend downward on
one definition rather than importing enums from each other. All are ``str``
enums, so a member serializes as its string value directly (into SQLite, JSON,
or a DataFrame column).
"""

from __future__ import annotations

from enum import Enum


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    BOTH = "both"


class Mode(str, Enum):
    """Where a signal/trade originated - the same schema serves all three."""
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class Disposition(str, Enum):
    """What happened to a recorded signal."""
    REJECTED = "rejected"                        # failed a gate / below floor
    SELECTED_NOT_FILLED = "selected_not_filled"  # chosen but not filled
    EXECUTED = "executed"                        # became a trade
    RECORDED_ONLY = "recorded_only"              # observed, never eligible


class StrategyStatus(str, Enum):
    """Strategy lifecycle states (see docs: Strategy Lifecycle)."""
    DRAFT = "draft"
    MEASURED = "measured"
    VALIDATED = "validated"
    APPROVED = "approved"
    PAPER = "paper"
    LIVE = "live"
    SUSPENDED = "suspended"
    RETIRED = "retired"
    REJECTED = "rejected"


class HoldingScope(str, Enum):
    """Intended holding horizon - drives product type and cost model."""
    INTRADAY = "intraday"   # MIS: opened and closed within one session
    SWING = "swing"         # CNC: held across sessions
