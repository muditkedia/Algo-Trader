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

    @property
    def risk_per_unit(self) -> float:
        return max(self.entry_ref - self.stop, 0.0)


def build_signal(strategy, prepared: pd.DataFrame, index: int,
                 timeframe: str, params: Optional[RiskParams] = None,
                 atr_value: Optional[float] = None,
                 swing_low: Optional[float] = None) -> Optional[TradingSignal]:
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
                         params)
    if stop is None or stop >= entry:
        return None
    target = _target(prepared, index, entry, stop, spec)
    target2 = None
    if spec.partial_fraction > 0 and spec.target2_col:
        t2 = float(prepared[spec.target2_col].iloc[index])
        if pd.notna(t2) and t2 > entry:
            target2 = t2
    try:
        conf = float(strategy.confidence(prepared.iloc[: index + 1]).score)
    except Exception:
        conf = 0.0
    bar_time = pd.Timestamp(row["date"])
    return TradingSignal(
        symbol=str(row.get("symbol", "")), strategy=strategy.name,
        timeframe=timeframe, bar_time=bar_time, entry_ref=entry, spec=spec,
        stop=float(stop), target=target, target2=target2, confidence=conf,
        session=str(bar_time.normalize().date()))
