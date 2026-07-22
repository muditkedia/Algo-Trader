"""TradeManager - live interpretation of each position's ExecutionSpec.

This is the production twin of the backtest engine's per-bar management loop
(`algo.execution.engine.execute_signal`), applied to LIVE positions one bar at
a time. It reuses the SAME conventions the verified engine pins:

  * stop first (pessimistic same-bar), honest gap fill at the open,
  * target (with the optional partial -> breakeven -> second target),
  * trailing (chandelier via the promoted risk fn, or a strategy `column`),
  * intraday square-off at/after the session cutoff.

It does NOT place orders itself - it returns a decision (hold / exit / partial
/ trail-move) plus the honest fill price, which the engine turns into an order
through the OrderManager. Keeping decision and execution separate is what lets
paper and live share this exact code: only the adapter differs.

Trailing-stop STATE lives on the Position (``stop``, ``trailed``), persisted by
the portfolio, so a restart resumes management from the ratcheted stop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from algo.execution.spec import ExecutionSpec
from algo.risk.engine import RiskParams, trailing_stop_price
from algo.trading.models import Position


@dataclass
class ManageDecision:
    action: str                 # hold | exit | partial | trail
    price: Optional[float] = None
    reason: str = ""
    new_stop: Optional[float] = None
    partial_qty: float = 0.0
    gap_fill: bool = False


class TradeManager:
    def __init__(self, specs: dict, params: Optional[RiskParams] = None) -> None:
        #: strategy name -> ExecutionSpec (frozen; loaded from the registry)
        self.specs = specs
        self.params = params or RiskParams()

    def spec_for(self, position: Position) -> ExecutionSpec:
        return self.specs[position.strategy]

    def manage(self, position: Position, bar: pd.Series,
               spec: Optional[ExecutionSpec] = None,
               past_squareoff: bool = False) -> ManageDecision:
        """Evaluate one just-closed bar against a position's spec. Order of
        checks matches the backtest engine exactly."""
        spec = spec or self.spec_for(position)
        o, h, l, c = (float(bar["open"]), float(bar["high"]),
                      float(bar["low"]), float(bar["close"]))
        stop = position.stop
        entry = position.entry_price
        is_long = position.is_long
        sign = position.sign
        bar_key = str(pd.Timestamp(bar.get("date")))
        is_new_bar = bar_key != position.last_managed_bar
        if is_new_bar:
            position.last_managed_bar = bar_key
            position.bars_held += 1
            position.highest_since_entry = max(position.highest_since_entry, h)
            position.lowest_since_entry = min(position.lowest_since_entry, l)

        # 1) stop first, honest gap fill at the open
        stop_hit = (l <= stop or o <= stop) if is_long else (h >= stop or o >= stop)
        if stop_hit:
            gap = o < stop if is_long else o > stop
            price = ((o if o < stop else stop) if is_long
                     else (o if o > stop else stop))
            at_be = stop >= entry if is_long else stop <= entry
            reason = ("breakeven_stop" if position.partial_done
                      and at_be else
                      ("trailing_stop" if position.trailed else "stop_loss"))
            return ManageDecision("exit", price=price, reason=reason,
                                  gap_fill=gap)

        # 2) target (honest gap fill), optional partial at the first target
        target = position.target
        target_hit = (target is not None and
                      ((h >= target or o >= target) if is_long
                       else (l <= target or o <= target)))
        if target_hit:
            fill = max(target, o) if is_long else min(target, o)
            if spec.partial_fraction > 0 and not position.partial_done:
                qty = position.open_quantity * spec.partial_fraction
                return ManageDecision("partial", price=fill, reason="partial",
                                      partial_qty=qty,
                                      new_stop=(max(stop, entry) if is_long
                                                else min(stop, entry)),
                                      gap_fill=(o > target if is_long
                                                else o < target))
            return ManageDecision("exit", price=fill, reason="target",
                                  gap_fill=(o > target if is_long
                                            else o < target))

        # 3) close-confirmed strategy invalidation / no-progress timeout
        invalidation_col = (spec.invalidation_long_col if is_long
                            else spec.invalidation_short_col)
        invalidation_col = invalidation_col or spec.invalidation_col
        if invalidation_col and bool(bar.get(invalidation_col, False)):
            return ManageDecision("exit", price=c,
                                  reason="structural_invalidation")
        if (is_new_bar and spec.timeout_bars is not None
                and position.timeout_target is not None
                and position.bars_held >= spec.timeout_bars):
            reached = (position.highest_since_entry >= position.timeout_target
                       if is_long else
                       position.lowest_since_entry <= position.timeout_target)
            if not reached:
                return ManageDecision("exit", price=c, reason="target_timeout")
        if (is_new_bar and spec.no_progress_bars is not None
                and position.bars_held >= spec.no_progress_bars):
            initial_risk = abs(position.entry_price - position.initial_stop)
            progress_r = (sign * (c - entry) / initial_risk
                          if initial_risk > 0 else float("-inf"))
            if progress_r < spec.no_progress_r:
                return ManageDecision("exit", price=c, reason="no_progress")

        # 4) intraday square-off (session rule)
        if spec.intraday and past_squareoff:
            return ManageDecision("exit", price=c, reason="session_squareoff")

        # 5) trailing (ratchet only), per the declaration
        new_stop = None
        if spec.trail == "chandelier" and (
                position.partial_done or not spec.trail_after_partial):
            if spec.trail_after_partial:
                cand = (position.highest_since_entry
                        - spec.trail_atr_mult * position.atr_at_entry
                        if is_long else position.lowest_since_entry
                        + spec.trail_atr_mult * position.atr_at_entry)
            else:
                cand = trailing_stop_price(
                    entry, c, sign * (c / entry - 1.0),
                    position.atr_at_entry, self.params)
            if cand is not None:
                bounded = (min(cand, c * (1 - 1e-4)) if is_long
                           else max(cand, c * (1 + 1e-4)))
                improves = bounded > stop if is_long else bounded < stop
                if improves:
                    new_stop = bounded
        elif spec.trail == "column" and spec.trail_col:
            cand = bar.get(spec.trail_col)
            if cand is not None and np.isfinite(cand):
                bounded = (min(float(cand), c * (1 - 1e-4)) if is_long
                           else max(float(cand), c * (1 + 1e-4)))
                improves = bounded > stop if is_long else bounded < stop
                if improves:
                    new_stop = bounded
        if new_stop is not None:
            return ManageDecision("trail", new_stop=new_stop, reason="trail")

        # 5) swing horizon (only swing specs carry a bar cap - intraday never)
        return ManageDecision("hold")
