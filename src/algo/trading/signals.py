"""Normalized trading signals - the orchestrator's output contract.

A TradingSignal carries everything downstream stages need and nothing
strategy-specific: the owning strategy's name and ExecutionSpec, the signal
bar, the reference entry price (the signal bar's close - the platform's
entry timing), and the ENTRY-TIME levels (initial stop, targets) computed
with the SAME helpers the verified backtest engine uses
(`algo.execution.engine._initial_stop` / `_target` - reuse, not
reimplementation, so live levels can never drift from backtest semantics).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from algo.execution.engine import _initial_stop, _target
from algo.execution.spec import ExecutionSpec
from algo.core.enums import Direction
from algo.risk.engine import RiskParams


@dataclass(frozen=True)
class TradingSignal:
    symbol: str
    strategy: str
    timeframe: str
    bar_time: pd.Timestamp            # the completed signal bar's open time
    entry_ref: float                  # signal bar close (entry reference)
    spec: ExecutionSpec
    stop: float
    target: Optional[float] = None
    target2: Optional[float] = None
    confidence: float = 0.0
    session: str = ""                 # signal bar's session date (ISO)
    direction: Direction = Direction.LONG
    trigger_price: float = 0.0
    limit_price: Optional[float] = None
    regime_score: float = 0.0          # normalized 0..1
    priority_score: float = 0.0        # normalized 0..1
    grade_multiplier: float = 1.0
    confidence_components: Optional[dict] = None
    reason: str = ""
    exclusive_group: str = ""
    atr_at_entry: float = 0.0
    structural_stop: float = 0.0

    @property
    def risk_per_unit(self) -> float:
        distance = (self.entry_ref - self.stop
                    if self.direction == Direction.LONG
                    else self.stop - self.entry_ref)
        return max(distance, 0.0)


def build_signal(strategy, prepared: pd.DataFrame, index: int,
                 timeframe: str, params: Optional[RiskParams] = None,
                 atr_value: Optional[float] = None,
                 swing_low: Optional[float] = None,
                 swing_high: Optional[float] = None,
                 direction: Direction = Direction.LONG) -> Optional[TradingSignal]:
    """Levels for the signal at ``prepared.iloc[index]`` per the strategy's
    OWN ExecutionSpec; None when the declaration makes it untradeable
    (exactly the backtest engine's entry-time rejections)."""
    spec: ExecutionSpec = strategy.execution
    params = params or RiskParams()
    row = prepared.iloc[index]
    entry = float(row["close"])
    atr_value = (float(row["atr"]) if atr_value is None
                 and "atr" in prepared.columns else (atr_value or 0.0))
    stop = _initial_stop(prepared, index, entry, spec, atr_value, swing_low,
                         params, direction=direction, swing_high=swing_high)
    invalid = stop is None or ((stop >= entry) if direction == Direction.LONG
                               else (stop <= entry))
    if invalid:
        return None
    target = _target(prepared, index, entry, stop, spec, direction)
    target2 = None
    if spec.partial_fraction > 0 and spec.target2_col:
        t2 = float(prepared[spec.target2_col].iloc[index])
        valid = t2 > entry if direction == Direction.LONG else t2 < entry
        if pd.notna(t2) and valid:
            target2 = t2
    try:
        scored = strategy.confidence_for(prepared.iloc[: index + 1], direction)
        conf = float(scored.score)
    except Exception:
        scored = None
        conf = 0.0
    regime = float(strategy.regime_score(prepared.iloc[: index + 1], direction))
    priority = 0.60 * conf + 0.40 * regime
    grade = 1.0 if conf >= 0.85 else (0.75 if conf >= 0.70 else 0.50)
    trigger = float(strategy.entry_trigger(prepared, index, direction))
    limit = None
    if spec.entry == "limit_collar":
        sign = 1.0 if direction == Direction.LONG else -1.0
        limit = trigger * (1.0 + sign * spec.slippage_collar_pct)
    bar_time = pd.Timestamp(row["date"])
    return TradingSignal(
        symbol=str(row.get("symbol", "")), strategy=strategy.name,
        timeframe=timeframe, bar_time=bar_time, entry_ref=entry, spec=spec,
        stop=float(stop), target=target, target2=target2, confidence=conf,
        session=str(bar_time.normalize().date()), direction=direction,
        trigger_price=trigger, limit_price=limit, regime_score=regime,
        priority_score=priority, grade_multiplier=grade,
        confidence_components=(scored.components if scored else {}),
        reason=(scored.reason if scored else ""),
        exclusive_group=strategy.meta.exclusive_group,
        atr_at_entry=float(atr_value),
        structural_stop=(float(row[spec.stop_col]) if spec.stop_col else 0.0))
