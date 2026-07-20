"""Strategy-agnostic trade executor - interprets each strategy's ExecutionSpec.

The engine executes exactly what the strategy declares (entry timing, stop,
target(s), partial, trailing, session restrictions, holding limit,
square-off) and nothing else. It fixes the three confirmed backtest bugs of
the pre-reset simulator:

1. **No overnight carry**: an intraday signal on the session's last bar is NOT
   tradeable (there is no same-session bar left to manage it) - it produces no
   trade instead of entering at the close and riding the overnight gap.
2. **No generic holding cap**: intraday positions run to the strategy's own
   exit (stop / target / trail / square-off at the session's actual last bar),
   not to a platform-imposed 8-bar limit. Swing strategies declare their own
   ``max_hold_bars``.
3. **Honest gap fills**: a bar that OPENS beyond the stop (or target) fills at
   the open, not at the level (the old simulator booked stop fills AT the stop
   through gaps - documented optimism).

Same-bar ambiguity keeps the platform's conservative convention: the stop is
checked before the target, so a bar spanning both is a stop-out.

Pure-function reuse from the promoted risk engine (``initial_stop_pct``,
``trailing_stop_price``) - the ATR/structure stop and the chandelier trail are
now per-strategy declarations, not platform impositions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

import numpy as np
import pandas as pd

from algo.core.costs import CostModel, Product
from algo.core.logging import get_logger
from algo.execution.spec import ExecutionSpec
from algo.risk.engine import RiskParams, initial_stop_pct, trailing_stop_price

logger = get_logger("execution.engine")


@dataclass
class ExecutedTrade:
    """Outcome of one executed signal (superset of the canonical trade row)."""

    symbol: str
    open_date: pd.Timestamp
    close_date: pd.Timestamp
    entry_price: float
    exit_price: float
    exit_reason: str
    holding_min: float
    stop_distance_pct: float
    quantity: float
    stake_amount: float
    gross_ratio: float
    cost_ratio: float
    profit_ratio: float          # NET
    profit_abs: float            # NET, account currency
    mfe_pct: float
    mae_pct: float
    partial: bool = False        # a partial leg was booked at the first target
    partial_price: Optional[float] = None
    gap_fill: bool = False       # stop/target filled at the open through a gap
    trailed: bool = False


def _session(ts) -> pd.Timestamp:
    return pd.Timestamp(ts).normalize()


def _initial_stop(bars: pd.DataFrame, i: int, entry: float,
                  spec: ExecutionSpec, atr: float,
                  swing_low: Optional[float],
                  params: RiskParams) -> Optional[float]:
    """The strategy's declared initial stop PRICE, or None (not tradeable)."""
    if spec.stop_kind == "column":
        level = float(bars[spec.stop_col].iloc[i])
        return level if np.isfinite(level) and level < entry else None
    # atr_structure: the declared ATR-multiple / swing-low distance, hard-capped
    p = replace(params, atr_stop_multiplier=spec.stop_atr_mult,
                hard_stop_pct=spec.hard_stop_pct or params.hard_stop_pct)
    stop_pct = initial_stop_pct(entry, atr, swing_low, p)
    if stop_pct is None:
        return None
    stop_pct = min(stop_pct, p.hard_stop_pct)
    return entry * (1.0 - stop_pct)


def _target(bars: pd.DataFrame, i: int, entry: float, stop: float,
            spec: ExecutionSpec) -> Optional[float]:
    if spec.target_kind == "none":
        return None
    if spec.target_kind == "r":
        risk = entry - stop
        return entry + spec.target_r * risk if risk > 0 else None
    level = float(bars[spec.target_col].iloc[i])
    return level if np.isfinite(level) and level > entry else None


def execute_signal(bars: pd.DataFrame, signal_index: int, *,
                   spec: ExecutionSpec, atr: float,
                   swing_low: Optional[float], cost_model: CostModel,
                   product: Product, stake: float,
                   params: Optional[RiskParams] = None
                   ) -> Optional[ExecutedTrade]:
    """Execute ONE long signal exactly per the strategy's declaration.

    ``bars`` is the strategy's prepared frame (date/open/high/low/close plus
    whatever level columns the spec references). Returns None when the
    declaration makes the signal untradeable (last bar of an intraday session,
    no valid stop level, no future bar).
    """
    params = params or RiskParams()
    i = int(signal_index)
    n = len(bars)
    if i >= n - 1:
        return None                                # no bar left to manage
    entry_row = bars.iloc[i]
    entry_day = _session(entry_row["date"])
    if spec.intraday and _session(bars["date"].iloc[i + 1]) != entry_day:
        return None                                # session's last bar: no entry

    entry = float(entry_row["close"])
    stop = _initial_stop(bars, i, entry, spec, atr, swing_low, params)
    if stop is None or stop >= entry:
        return None                                # declared stop unavailable
    initial_stop = stop
    target = _target(bars, i, entry, stop, spec)
    target2 = None
    if spec.partial_fraction > 0 and spec.target2_col:
        t2 = float(bars[spec.target2_col].iloc[i])
        target2 = t2 if np.isfinite(t2) and t2 > entry else None

    qty = stake / entry
    open_frac = 1.0
    realized = 0.0                                 # gross P&L of booked partial
    partial_done = False
    partial_price: Optional[float] = None
    trailed = False
    gap_fill = False
    mfe = mae = 0.0
    exit_price: Optional[float] = None
    exit_reason = ""
    exit_j = i

    j = i + 1
    while j < n:
        row = bars.iloc[j]
        bar_day = _session(row["date"])
        if spec.intraday and bar_day != entry_day:
            break                                  # safety: never cross a session
        o, h, l, c = (float(row["open"]), float(row["high"]),
                      float(row["low"]), float(row["close"]))
        exit_j = j

        # 1) stop first (conservative same-bar convention), honest gap fill
        if l <= stop or o <= stop:
            gap_fill = o < stop
            exit_price = o if o < stop else stop
            if partial_done and stop >= entry:
                exit_reason = "breakeven_stop"
            elif trailed:
                exit_reason = "trailing_stop"
            else:
                exit_reason = "stop_loss"
            mae = min(mae, exit_price / entry - 1.0)
            break

        # 2) target (honest gap fill), optional partial at the first target
        if target is not None and (h >= target or o >= target):
            t_fill = max(target, o)
            if spec.partial_fraction > 0 and not partial_done:
                realized += (t_fill - entry) * qty * spec.partial_fraction
                open_frac -= spec.partial_fraction
                partial_done, partial_price = True, t_fill
                stop = max(stop, entry)            # breakeven on the remainder
                target = target2
            else:
                gap_fill = gap_fill or o > target
                exit_price, exit_reason = t_fill, "target"
                mfe = max(mfe, t_fill / entry - 1.0)
                break

        mfe = max(mfe, h / entry - 1.0)
        mae = min(mae, l / entry - 1.0)

        # 3) intraday square-off at the session's actual last bar
        if spec.intraday:
            last_of_session = (j == n - 1
                               or _session(bars["date"].iloc[j + 1]) != bar_day)
            if last_of_session:
                exit_price, exit_reason = c, "session_squareoff"
                break

        # 4) trailing per the declaration (chandelier + profit locks, or a
        #    strategy-computed level column - both only ever ratchet upward)
        if spec.trail == "chandelier":
            cand = trailing_stop_price(entry, c, c / entry - 1.0, atr, params)
            if cand is not None:
                raised = min(cand, c * (1 - 1e-4))
                if raised > stop:
                    stop, trailed = raised, True
        elif spec.trail == "column":
            cand = float(bars[spec.trail_col].iloc[j])
            if np.isfinite(cand):
                raised = min(cand, c * (1 - 1e-4))
                if raised > stop:
                    stop, trailed = raised, True

        # 5) the strategy's own holding limit (swing horizon)
        if spec.max_hold_bars is not None and j - i >= spec.max_hold_bars:
            exit_price, exit_reason = c, "horizon_end"
            break
        if j == n - 1:                             # data ends while open
            exit_price = c
            exit_reason = ("session_squareoff" if spec.intraday
                           else "horizon_end")
            break
        j += 1

    if exit_price is None:                         # loop never ran a full bar
        return None

    gross_abs = realized + (exit_price - entry) * qty * open_frac
    legs = ([(partial_price, spec.partial_fraction)] if partial_done else [])
    legs.append((exit_price, open_frac))
    buy_cost = cost_model.side_cost(price=entry, quantity=qty, is_buy=True,
                                    product=product)
    sell_cost = sum(cost_model.side_cost(price=p, quantity=qty * f,
                                         is_buy=False, product=product)
                    for p, f in legs)
    gross_ratio = gross_abs / stake
    cost_ratio = (buy_cost + sell_cost) / stake
    profit_ratio = gross_ratio - cost_ratio
    exit_date = pd.Timestamp(bars["date"].iloc[exit_j])

    return ExecutedTrade(
        symbol=str(entry_row.get("symbol", "")),
        open_date=pd.Timestamp(entry_row["date"]), close_date=exit_date,
        entry_price=entry, exit_price=float(exit_price),
        exit_reason=exit_reason,
        holding_min=(exit_date - pd.Timestamp(entry_row["date"])
                     ).total_seconds() / 60.0,
        stop_distance_pct=(entry - initial_stop) / entry,
        quantity=qty, stake_amount=stake,
        gross_ratio=float(gross_ratio), cost_ratio=float(cost_ratio),
        profit_ratio=float(profit_ratio),
        profit_abs=float(profit_ratio * stake),
        mfe_pct=float(mfe), mae_pct=float(mae),
        partial=partial_done, partial_price=partial_price,
        gap_fill=bool(gap_fill), trailed=bool(trailed))
