"""ExecutionSpec - the declarative execution interface every strategy owns.

A strategy's ``execution`` class attribute fully defines its trading
behaviour beyond the entry signal:

* entry timing (how a signal becomes a fill),
* initial stop-loss (structural level or ATR/structure distance),
* profit target (none, a level column, or an R-multiple), with an optional
  partial exit at the first target,
* trailing rules (none, or the chandelier ATR trail + profit-lock ladder),
* session restrictions (intraday strategies never enter on the session's last
  bar and never hold overnight),
* maximum holding period, and
* square-off logic (session's last bar for intraday; horizon end for swing).

The engine (`algo.execution.engine`) interprets this declaration and nothing
else - there is no platform-imposed stop, trail, or exit. Strategies whose
intended behaviour is identical share the constructor helpers below; the
declaration still lives in each strategy's own module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ExecutionSpec:
    """One strategy's complete execution declaration."""

    #: ``signal_close`` uses the completed-bar close. ``limit_collar`` submits
    #: a marketable limit around the strategy's trigger and creates a position
    #: only after the broker confirms a fill.
    entry: str = "signal_close"
    slippage_collar_pct: float = 0.0

    # ---------------------------------------------------------- stop-loss
    #: ``column``: a structural level computed by the strategy's own
    #: ``prepare`` (e.g. the opening-range low). ``atr_structure``: the wider
    #: of ``stop_atr_mult`` x ATR and the recent swing low, capped at
    #: ``hard_stop_pct`` (the pre-reset behaviour, now owned per strategy).
    stop_kind: str = "atr_structure"
    stop_col: Optional[str] = None
    stop_long_col: Optional[str] = None
    stop_short_col: Optional[str] = None
    stop_atr_mult: float = 2.0
    hard_stop_pct: Optional[float] = 0.06
    #: ``column_atr_cap`` combines a structural column with a maximum distance
    #: from entry (long=max(column, entry-mult*ATR), short=min(...)).

    # ------------------------------------------------------- profit target
    #: ``none`` | ``column`` (level from ``target_col``) | ``r``
    #: (entry + ``target_r`` x initial risk).
    target_kind: str = "none"
    target_col: Optional[str] = None
    target_r: float = 0.0
    target_r_long_col: Optional[str] = None
    target_r_short_col: Optional[str] = None
    #: Optional partial at the first target: fraction booked there, stop moves
    #: to breakeven. The remainder runs to ``target2_col`` (if any), where it
    #: either exits or books ``target2_partial_fraction`` and leaves a runner.
    partial_fraction: float = 0.0
    target2_col: Optional[str] = None
    target2_long_col: Optional[str] = None
    target2_short_col: Optional[str] = None
    #: When positive, target 2 books this additional fraction of the original
    #: quantity and leaves the rest to the declared trail/square-off.  Zero
    #: preserves the existing full-exit-at-target-2 behaviour.
    target2_partial_fraction: float = 0.0
    #: Re-read the target-2 directional column from every managed bar rather
    #: than freezing it at entry (for a moving channel boundary).
    dynamic_target2: bool = False

    # ------------------------------------------------------------ trailing
    #: ``none`` | ``chandelier`` (ATR trail + profit-lock ladder from the
    #: promoted risk engine; activates at its configured profit) | ``column``
    #: (trail along a level the strategy computes per bar in ``trail_col`` -
    #: e.g. a supertrend line or a rising VWAP; the engine only ever ratchets
    #: the stop upward, never widens it).
    trail: str = "none"
    trail_col: Optional[str] = None
    trail_atr_mult: float = 2.0
    trail_after_partial: bool = False

    # ------------------------------------------------------- invalidation
    #: Boolean prepared-frame column that closes an open trade when true.
    invalidation_col: Optional[str] = None
    invalidation_long_col: Optional[str] = None
    invalidation_short_col: Optional[str] = None
    #: Close after this many completed bars when progress is below the stated
    #: R multiple. Both must be set together.
    no_progress_bars: Optional[int] = None
    no_progress_r: float = 0.0
    #: Exit after ``timeout_bars`` unless price has touched the directional
    #: entry-time target column (for level-specific time-decay rules).
    timeout_bars: Optional[int] = None
    timeout_target_long_col: Optional[str] = None
    timeout_target_short_col: Optional[str] = None

    # -------------------------------------------------------------- sizing
    #: Strategy ceilings; the account risk engine always applies the stricter
    #: of these and the central portfolio limits.
    risk_per_trade_pct: Optional[float] = None
    max_capital_per_trade: Optional[float] = None

    # ------------------------------------- session / holding / square-off
    #: Intraday: entries are forbidden on the session's last bar, positions
    #: never cross a session boundary and square off at the session's last
    #: bar close. Swing: positions may hold overnight up to ``max_hold_bars``.
    intraday: bool = True
    allow_overnight: bool = False
    #: Bar cap on the holding period. ``None`` means the session governs
    #: (intraday) - there is no generic bar cap.
    max_hold_bars: Optional[int] = None

    def __post_init__(self) -> None:
        if self.entry not in ("signal_close", "limit_collar"):
            raise ValueError(f"unsupported entry timing {self.entry!r}")
        if self.entry == "limit_collar" and self.slippage_collar_pct <= 0:
            raise ValueError("limit_collar entry needs a positive collar")
        if self.stop_kind not in ("column", "column_atr_cap",
                                  "atr_structure"):
            raise ValueError(f"unknown stop_kind {self.stop_kind!r}")
        if self.stop_kind in ("column", "column_atr_cap") \
                and not self.stop_col \
                and not (self.stop_long_col and self.stop_short_col):
            raise ValueError(
                f"stop_kind={self.stop_kind!r} needs stop_col or both "
                "directional stop columns")
        if self.target_kind not in ("none", "column", "r"):
            raise ValueError(f"unknown target_kind {self.target_kind!r}")
        if self.target_kind == "column" and not self.target_col:
            raise ValueError("target_kind='column' needs target_col")
        if self.target_kind == "r" and self.target_r <= 0:
            raise ValueError("target_kind='r' needs target_r > 0")
        if self.partial_fraction and self.target_kind == "none":
            raise ValueError("a partial exit needs a first target")
        if not 0.0 <= self.partial_fraction < 1.0:
            raise ValueError("partial_fraction must be in [0, 1)")
        if not 0.0 <= self.target2_partial_fraction < 1.0:
            raise ValueError("target2_partial_fraction must be in [0, 1)")
        if self.partial_fraction + self.target2_partial_fraction >= 1.0:
            raise ValueError("partial exits must leave a positive runner")
        if self.target2_partial_fraction and not (
                self.target2_col
                or (self.target2_long_col and self.target2_short_col)):
            raise ValueError("a target-2 partial needs target-2 columns")
        if self.dynamic_target2 and not (
                self.target2_col
                or (self.target2_long_col and self.target2_short_col)):
            raise ValueError("dynamic_target2 needs target-2 columns")
        if self.trail not in ("none", "chandelier", "column"):
            raise ValueError(f"unknown trail {self.trail!r}")
        if self.trail == "column" and not self.trail_col:
            raise ValueError("trail='column' needs trail_col")
        if self.trail_atr_mult <= 0:
            raise ValueError("trail_atr_mult must be positive")
        if (self.no_progress_bars is None) != (self.no_progress_r == 0.0):
            raise ValueError("no-progress bars and R threshold must be set together")
        if self.no_progress_bars is not None and self.no_progress_bars < 1:
            raise ValueError("no_progress_bars must be positive")
        if self.timeout_bars is not None and self.timeout_bars < 1:
            raise ValueError("timeout_bars must be positive")
        if self.timeout_bars is not None and not (
                self.timeout_target_long_col and self.timeout_target_short_col):
            raise ValueError("timeout_bars needs directional target columns")
        if self.risk_per_trade_pct is not None \
                and not 0 < self.risk_per_trade_pct <= 1:
            raise ValueError("risk_per_trade_pct must be in (0, 1]")
        if self.max_capital_per_trade is not None \
                and not 0 < self.max_capital_per_trade <= 1:
            raise ValueError("max_capital_per_trade must be in (0, 1]")
        if self.intraday and self.allow_overnight:
            raise ValueError("intraday specs cannot allow overnight holds")
        if not self.intraday and self.max_hold_bars is None:
            raise ValueError("swing specs need an explicit max_hold_bars")


# ------------------------------------------------------- shared constructors
# Helpers for strategies whose intended behaviour is identical. The
# DECLARATION still lives in each strategy module (ownership); these only
# remove copy-paste of the field values.

def structural_intraday(*, stop_col: str, target_kind: str = "none",
                        target_col: Optional[str] = None,
                        target_r: float = 0.0,
                        partial_fraction: float = 0.0,
                        target2_col: Optional[str] = None) -> ExecutionSpec:
    """Published-form intraday execution: structural stop level, optional
    target(s), no trail, session square-off, never overnight."""
    return ExecutionSpec(
        stop_kind="column", stop_col=stop_col, hard_stop_pct=None,
        target_kind=target_kind, target_col=target_col, target_r=target_r,
        partial_fraction=partial_fraction, target2_col=target2_col,
        trail="none", intraday=True, allow_overnight=False,
        max_hold_bars=None)


def atr_trail_intraday() -> ExecutionSpec:
    """ATR/structure stop + chandelier trail, session square-off, never
    overnight - for intraday strategies with no published exit of their own
    (their pre-reset declared behaviour, now strategy-owned)."""
    return ExecutionSpec(
        stop_kind="atr_structure", target_kind="none", trail="chandelier",
        intraday=True, allow_overnight=False, max_hold_bars=None)


def atr_trail_swing(max_hold_bars: int) -> ExecutionSpec:
    """ATR/structure stop + chandelier trail, overnight allowed, horizon end
    at ``max_hold_bars`` - the swing/positional profile."""
    return ExecutionSpec(
        stop_kind="atr_structure", target_kind="none", trail="chandelier",
        intraday=False, allow_overnight=True,
        max_hold_bars=int(max_hold_bars))
