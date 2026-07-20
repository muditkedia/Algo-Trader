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
    """One strategy's complete execution declaration (long-only)."""

    #: How a signal becomes a fill. ``signal_close`` (the only implemented
    #: mode) fills at the close of the bar whose close-confirmed signal fired -
    #: the honest, implementable timing for close-confirmed entry conditions
    #: (trigger-level resting orders were measured in the Phase-17 fidelity
    #: audit to carry an information advantage bar data cannot honestly fill).
    entry: str = "signal_close"

    # ---------------------------------------------------------- stop-loss
    #: ``column``: a structural level computed by the strategy's own
    #: ``prepare`` (e.g. the opening-range low). ``atr_structure``: the wider
    #: of ``stop_atr_mult`` x ATR and the recent swing low, capped at
    #: ``hard_stop_pct`` (the pre-reset behaviour, now owned per strategy).
    stop_kind: str = "atr_structure"
    stop_col: Optional[str] = None
    stop_atr_mult: float = 2.0
    hard_stop_pct: Optional[float] = 0.06

    # ------------------------------------------------------- profit target
    #: ``none`` | ``column`` (level from ``target_col``) | ``r``
    #: (entry + ``target_r`` x initial risk).
    target_kind: str = "none"
    target_col: Optional[str] = None
    target_r: float = 0.0
    #: Optional partial at the first target: fraction booked there, stop moves
    #: to breakeven, remainder runs to ``target2_col`` (if any) else to
    #: stop / square-off.
    partial_fraction: float = 0.0
    target2_col: Optional[str] = None

    # ------------------------------------------------------------ trailing
    #: ``none`` | ``chandelier`` (ATR trail + profit-lock ladder from the
    #: promoted risk engine; activates at its configured profit) | ``column``
    #: (trail along a level the strategy computes per bar in ``trail_col`` -
    #: e.g. a supertrend line or a rising VWAP; the engine only ever ratchets
    #: the stop upward, never widens it).
    trail: str = "none"
    trail_col: Optional[str] = None

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
        if self.entry != "signal_close":
            raise ValueError(f"unsupported entry timing {self.entry!r}")
        if self.stop_kind not in ("column", "atr_structure"):
            raise ValueError(f"unknown stop_kind {self.stop_kind!r}")
        if self.stop_kind == "column" and not self.stop_col:
            raise ValueError("stop_kind='column' needs stop_col")
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
        if self.trail not in ("none", "chandelier", "column"):
            raise ValueError(f"unknown trail {self.trail!r}")
        if self.trail == "column" and not self.trail_col:
            raise ValueError("trail='column' needs trail_col")
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
