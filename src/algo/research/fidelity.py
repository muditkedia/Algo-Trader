"""Fidelity Evaluation Mode - research-only execution per PUBLISHED semantics.

The production engine represents "how we trade" (close-of-bar entry, uniform
ATR stop + chandelier trail, no targets - D-006/D-023, frozen). This module
represents "how the published strategy was designed to trade": trigger-level
stop entries, structural stops, fixed-R / level targets, partial exits. It is a
SEPARATE research path (D-036 Part F): nothing here is imported by the
production simulator, risk engine, paper engine, or promotion workflow, and no
verdict from it feeds the frozen D-031 gate - it exists to answer one question
scientifically: does a published system's edge survive when executed as
published?

Execution conventions (all deliberate, all documented):

* ENTRY - three research modes:
    - ``trigger``  : a resting stop order at the strategy's trigger level fills
                     on the SIGNAL bar at max(trigger, open) (gap-open above the
                     level fills at the open). OHLC limitation: with bar data we
                     cannot see the intrabar path; because the production signal
                     requires a CLOSE beyond the level, the bar's high touched
                     the trigger, and we assume the touch was fillable. NOTE:
                     conditioning on the close-confirmed signal set is an
                     information advantage a real resting order does not have
                     (it also fills on touch-and-fail bars that never confirm),
                     so measured entry improvement is an UPPER BOUND.
    - ``close``    : the production convention (signal bar close) - parity mode.
    - ``next_open``: next bar's open (classic conservative research assumption).
* STOP - filled at the stop level, or at the OPEN when the bar gaps through it
  (honest gap-through; the production simulator books AT the stop - documented
  optimism this mode does not share).
* TARGET - filled at the target level, or at the OPEN when the bar gaps beyond
  it. Same-bar ambiguity (bar spans stop AND target): STOP FIRST - conservative,
  matching the production convention.
* PARTIAL - one partial supported: book ``partial_fraction`` at the target, move
  the stop to entry (breakeven) on the remainder, run the rest to a second
  target / stop / square-off.
* TRAILING - ``none`` or the promoted ATR chandelier (pure-function reuse of
  ``risk.engine.trailing_stop_price``; reuse, not modification).
* EOD - intraday square-off on the session's last bar (market rule).
* COSTS - the same NseEquityCostModel; partial legs pay proportional costs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from algo.core.costs import CostModel, Product
from algo.core.logging import get_logger
from algo.risk.engine import RiskParams, trailing_stop_price

logger = get_logger("research.fidelity")


@dataclass(frozen=True)
class ExecutionSpec:
    """A strategy's PUBLISHED execution semantics, declared for research."""

    name: str
    #: 'trigger' | 'close' | 'next_open'
    entry_mode: str = "close"
    #: prepared-frame column holding the entry trigger level ('trigger' mode)
    trigger_col: Optional[str] = None
    #: stop: 'column' (structural level from ``stop_col``) or 'atr'
    stop_mode: str = "column"
    stop_col: Optional[str] = None
    stop_atr_mult: float = 2.0
    #: target: 'none' | 'r' (``target_r`` x initial risk) | 'column'
    target_mode: str = "none"
    target_r: float = 2.0
    target_col: Optional[str] = None
    #: optional partial exit at the (first) target: fraction booked, stop->BE,
    #: remainder runs to ``target2_col`` (or R-doubling) / stop / square-off
    partial_fraction: float = 0.0
    target2_col: Optional[str] = None
    #: trailing on the (remaining) position: 'none' | 'atr'
    trail: str = "none"
    #: research-only extra preparation (adds fidelity columns to the prepared
    #: frame, e.g. pullback_low, floor-pivot targets) - never touches production
    extra_prep: Optional[Callable[[pd.DataFrame], pd.DataFrame]] = None


# ---------------------------------------------------------------- helpers

def floor_pivots(frame: pd.DataFrame) -> pd.DataFrame:
    """Prior-session floor-pivot levels (pivot, R1, R2), causal, per bar."""
    out = frame.copy()
    day = out["date"].dt.normalize()
    agg = out.groupby(day).agg(h=("high", "max"), l=("low", "min"),
                               c=("close", "last"))
    pivot = (agg["h"] + agg["l"] + agg["c"]) / 3.0
    r1 = 2.0 * pivot - agg["l"]
    r2 = pivot + (agg["h"] - agg["l"])
    out["fp_pivot"] = day.map(pivot.shift(1))
    out["fp_r1"] = day.map(r1.shift(1))
    out["fp_r2"] = day.map(r2.shift(1))
    return out


def _entry_fill(bars: pd.DataFrame, i: int, spec: ExecutionSpec):
    """(entry_index, entry_price) per the spec's entry mode, or None."""
    row = bars.iloc[i]
    if spec.entry_mode == "close":
        return i, float(row["close"])
    if spec.entry_mode == "next_open":
        if i + 1 >= len(bars):
            return None
        nxt = bars.iloc[i + 1]
        same_day = pd.Timestamp(nxt["date"]).normalize() == \
            pd.Timestamp(row["date"]).normalize()
        if not same_day:                      # signal on the last bar of a day
            return None
        return i + 1, float(nxt["open"])
    if spec.entry_mode == "trigger":
        level = float(row[spec.trigger_col]) if spec.trigger_col else np.nan
        if not np.isfinite(level) or float(row["high"]) < level:
            return None                       # trigger never touched: no fill
        return i, max(level, float(row["open"]))
    raise ValueError(f"unknown entry_mode {spec.entry_mode!r}")


def _initial_stop(bars: pd.DataFrame, i: int, entry: float,
                  spec: ExecutionSpec, atr: float,
                  params: RiskParams) -> Optional[float]:
    if spec.stop_mode == "column" and spec.stop_col:
        level = float(bars[spec.stop_col].iloc[i])
        if np.isfinite(level) and level < entry:
            return level
        return None                           # published stop not available
    if spec.stop_mode == "atr":
        return entry - params.atr_stop_multiplier * atr if atr > 0 else None
    raise ValueError(f"unknown stop_mode {spec.stop_mode!r}")


def _target_level(bars, i, entry, stop, spec) -> Optional[float]:
    if spec.target_mode == "none":
        return None
    if spec.target_mode == "r":
        risk = entry - stop
        return entry + spec.target_r * risk if risk > 0 else None
    if spec.target_mode == "column" and spec.target_col:
        level = float(bars[spec.target_col].iloc[i])
        return level if np.isfinite(level) and level > entry else None
    raise ValueError(f"unknown target_mode {spec.target_mode!r}")


# --------------------------------------------------------------- simulator

def simulate_fidelity(bars: pd.DataFrame, signal_index: int, *,
                      spec: ExecutionSpec, atr: float,
                      cost_model: CostModel, params: RiskParams,
                      stake: float = 100_000.0) -> Optional[dict]:
    """Replay ONE long trade under the published execution spec (intraday).

    Returns a canonical-plus record (incl. gross/net and the fill prices) or
    None when the spec produces no fill / no valid stop.
    """
    fill = _entry_fill(bars, signal_index, spec)
    if fill is None:
        return None
    entry_i, entry = fill
    stop = _initial_stop(bars, signal_index, entry, spec, atr, params)
    if stop is None or stop >= entry:
        return None
    target = _target_level(bars, signal_index, entry, stop, spec)
    target2 = None
    if spec.partial_fraction > 0:
        if spec.target2_col:
            t2 = float(bars[spec.target2_col].iloc[signal_index])
            target2 = t2 if np.isfinite(t2) and t2 > entry else None
        elif spec.target_mode == "r":
            target2 = entry + 2 * spec.target_r * (entry - stop)

    day = pd.Timestamp(bars["date"].iloc[entry_i]).normalize()
    qty = stake / entry
    open_frac = 1.0                            # fraction still open
    realized = 0.0                             # gross P&L of booked partials
    partial_done = False
    partial_price = None                       # the partial leg's fill price
    exit_price = None
    exit_reason = "session_squareoff"
    exit_i = entry_i

    j = entry_i + 1
    n = len(bars)
    while j < n:
        row = bars.iloc[j]
        if pd.Timestamp(row["date"]).normalize() != day:
            j -= 1
            break
        o, h, l, c = (float(row["open"]), float(row["high"]),
                      float(row["low"]), float(row["close"]))
        # 1) stop first (conservative same-bar convention)
        if l <= stop:
            exit_price = min(stop, o) if o < stop else stop   # honest gap fill
            exit_reason = "stop_loss" if not partial_done else "be_stop"
            exit_i = j
            break
        # 2) target
        if target is not None and h >= target:
            t_fill = max(target, o)
            if spec.partial_fraction > 0 and not partial_done:
                realized += (t_fill - entry) * qty * spec.partial_fraction
                open_frac -= spec.partial_fraction
                partial_done = True
                partial_price = t_fill
                stop = max(stop, entry)        # breakeven on the remainder
                target = target2               # run to the second target (or None)
            else:
                exit_price, exit_reason, exit_i = t_fill, "target", j
                break
        # 3) trail (optional)
        if spec.trail == "atr":
            cand = trailing_stop_price(entry, c, c / entry - 1.0, atr, params)
            if cand is not None:
                stop = max(stop, min(cand, c * (1 - 1e-4)))
        exit_i = j
        j += 1
    else:
        j = n - 1

    if exit_price is None:                     # ran to the session's last bar
        exit_price = float(bars["close"].iloc[exit_i])
        exit_reason = "session_squareoff" if exit_reason != "target" \
            else exit_reason

    gross_abs = realized + (exit_price - entry) * qty * open_frac
    gross_ratio = gross_abs / stake
    # costs: entry once, exits per leg (proportional quantities)
    legs = []
    if partial_done:
        legs.append((partial_price, spec.partial_fraction))
    legs.append((exit_price, open_frac))
    buy_cost = cost_model.side_cost(price=entry, quantity=qty, is_buy=True,
                                    product=Product.INTRADAY)
    sell_cost = sum(cost_model.side_cost(price=p, quantity=qty * f,
                                         is_buy=False, product=Product.INTRADAY)
                    for p, f in legs)
    cost_ratio = (buy_cost + sell_cost) / stake
    net_ratio = gross_ratio - cost_ratio

    return {
        "open_date": bars["date"].iloc[entry_i],
        "close_date": bars["date"].iloc[exit_i],
        "entry_price": entry, "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "gross_ratio": float(gross_ratio), "profit_ratio": float(net_ratio),
        "profit_abs": float(net_ratio * stake), "stake_amount": stake,
        "trade_duration": (pd.Timestamp(bars["date"].iloc[exit_i])
                           - pd.Timestamp(bars["date"].iloc[entry_i])
                           ).total_seconds() / 60.0,
        "stop_distance_pct": (entry - stop) / entry if stop < entry else np.nan,
        "partial": partial_done,
    }


def run_fidelity(prepared_frames: Dict[str, pd.DataFrame],
                 signals: Dict[str, pd.Series], spec: ExecutionSpec, *,
                 cost_model: CostModel, params: Optional[RiskParams] = None,
                 atr_period: int = 14) -> pd.DataFrame:
    """Simulate every signal under ``spec``; returns the canonical trades frame."""
    from algo.core.indicators import atr as atr_series

    params = params or RiskParams()
    records: List[dict] = []
    for symbol, frame in prepared_frames.items():
        sig = signals.get(symbol)
        if sig is None:
            continue
        work = frame if spec.extra_prep is None else spec.extra_prep(frame)
        if "atr" not in work.columns:
            work = work.copy()
            work["atr"] = atr_series(work, atr_period)
        for i in np.flatnonzero(sig.to_numpy(bool)):
            trade = simulate_fidelity(
                work, int(i), spec=spec, atr=float(work["atr"].iloc[i]),
                cost_model=cost_model, params=params)
            if trade is not None:
                trade["pair"] = symbol
                trade["enter_tag"] = spec.name
                trade["exit_reason_"] = trade["exit_reason"]
                records.append(trade)
    return pd.DataFrame(records)


# ------------------------------------------- the five published specs (D-036)

def _orb_prep(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["orb_target"] = out["or_high"] + (out["or_high"] - out["or_low"])
    return out


def _fp_prep(df: pd.DataFrame) -> pd.DataFrame:
    """Recompute the first-pullback bar's LOW (the published stop) - research-
    side mirror of the production strategy's internal mask; production code is
    untouched."""
    out = df.copy()
    day = out["date"].dt.normalize()
    # the pullback bar is where pullback_high was stamped (first non-ffilled)
    stamped = out["pullback_high"].notna() & out["pullback_high"].ne(
        out["pullback_high"].groupby(day).shift(1))
    out["pullback_low"] = out["low"].where(stamped).groupby(day).ffill()
    return out


def _vwap_stop_prep(df: pd.DataFrame) -> pd.DataFrame:
    """Published VWAP stops: 'just below VWAP' / 'below the dip low'."""
    out = df.copy()
    day = out["date"].dt.normalize()
    out["dip_low"] = out["low"].groupby(day).rolling(
        4, min_periods=1).min().reset_index(level=0, drop=True)
    return out


FIDELITY_SPECS: Dict[str, ExecutionSpec] = {
    "orb_15m": ExecutionSpec(
        name="orb_15m", entry_mode="trigger", trigger_col="or_high",
        stop_mode="column", stop_col="or_low",
        target_mode="column", target_col="orb_target",     # 1x range
        extra_prep=_orb_prep),
    "vwap_pullback_15m": ExecutionSpec(
        name="vwap_pullback_15m", entry_mode="trigger", trigger_col="high_prev",
        stop_mode="column", stop_col="vwap",               # just below VWAP
        target_mode="r", target_r=2.0,
        extra_prep=lambda df: df.assign(high_prev=df["high"].shift(1))),
    "vwap_15m": ExecutionSpec(
        name="vwap_15m", entry_mode="close",               # published IS close-based
        stop_mode="column", stop_col="dip_low",            # below the dip low
        target_mode="r", target_r=2.0, extra_prep=_vwap_stop_prep),
    "cpr_breakout_15m": ExecutionSpec(
        name="cpr_breakout_15m", entry_mode="trigger", trigger_col="cpr_top",
        stop_mode="column", stop_col="cpr_bot",            # below the CPR
        target_mode="column", target_col="fp_r1",          # R1, partial, run to R2
        partial_fraction=0.5, target2_col="fp_r2",
        extra_prep=floor_pivots),
    "first_pullback_15m": ExecutionSpec(
        name="first_pullback_15m", entry_mode="trigger",
        trigger_col="pullback_high",
        stop_mode="column", stop_col="pullback_low",       # under the pullback
        target_mode="r", target_r=2.0, extra_prep=_fp_prep),
}
