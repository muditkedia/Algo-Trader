"""ScanEngine - the concrete scanner infrastructure (no strategies shipped).

Each scan cycle processes EVERY eligible symbol: it loads the symbol's recent
bars (point-in-time, up to ``as_of``), runs every enabled strategy's entry
signal, and turns firing candidates into ranked ``Opportunity`` objects - the
unified opportunity interface. With zero strategies registered it still walks the
whole universe and returns an empty, timed result; that loop, its performance,
and the evidence-recording seam are the Phase-2 deliverable.

Reuses Phase 1 wholesale: the ``Scanner`` ABC, ``Opportunity`` +
``rank_opportunities``, the ``StrategyProfile`` interface, and the
``EvidenceLogger`` (every firing candidate is recorded, disposition
``recorded_only`` - there is no selection/confidence/trading yet).

Two pluggable seams keep later phases from touching this file:
  * ``prepare_fn(bars) -> bars``  - indicator enrichment (Phase 3).
  * ``score_fn(symbol, strategy, bars) -> (confidence, extras)`` - confidence
    engine (Phase 4). Default yields confidence 0.0.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import pandas as pd

from algo.core.logging import get_logger
from algo.evidence.models import Disposition, Mode, Signal
from algo.scanner.base import Opportunity, Scanner, rank_opportunities
from algo.strategies.base import StrategyProfile

logger = get_logger("scanner.engine")


@dataclass
class ScanResult:
    as_of: object
    timeframe: str
    opportunities: List[Opportunity] = field(default_factory=list)
    n_symbols_requested: int = 0
    n_symbols_with_data: int = 0
    n_candidates: int = 0
    duration_ms: float = 0.0

    def summary(self) -> dict:
        return {"as_of": str(self.as_of), "timeframe": self.timeframe,
                "requested": self.n_symbols_requested,
                "with_data": self.n_symbols_with_data,
                "candidates": self.n_candidates,
                "opportunities": len(self.opportunities),
                "duration_ms": round(self.duration_ms, 2)}


def _default_score(symbol, strategy, bars):
    return 0.0, {}


class ScanEngine(Scanner):
    def __init__(self, store, strategies: List[StrategyProfile],
                 timeframe: str = "1d", lookback_bars: int = 200,
                 evidence_logger=None, mode: str = Mode.BACKTEST.value,
                 prepare_fn: Optional[Callable] = None,
                 score_fn: Optional[Callable] = None) -> None:
        self.store = store
        self.strategies = [s for s in strategies if s.enabled]
        self.timeframe = timeframe
        self.lookback_bars = lookback_bars
        self.evidence_logger = evidence_logger
        self.mode = mode
        self.prepare_fn = prepare_fn or (lambda bars: bars)
        self.score_fn = score_fn or _default_score
        # Map strategy name -> evidence strategy_id (idempotent registration).
        self._strategy_ids: Dict[str, int] = {}
        if self.evidence_logger is not None:
            for strat in self.strategies:
                self._strategy_ids[strat.name] = \
                    self.evidence_logger.register_strategy(
                        strat.name, strat.meta.version)

    # ------------------------------------------------------------------ scan

    def scan(self, as_of, symbols: Optional[List[str]] = None) -> ScanResult:
        symbols = list(symbols or [])
        result = ScanResult(as_of=as_of, timeframe=self.timeframe,
                            n_symbols_requested=len(symbols))
        started = time.perf_counter()
        for symbol in symbols:
            bars = self.store.read(symbol, self.timeframe, end=as_of)
            if bars.empty:
                continue
            bars = self.prepare_fn(bars.tail(self.lookback_bars)
                                   .reset_index(drop=True))
            result.n_symbols_with_data += 1
            for strat in self.strategies:
                signal = strat.entry_signal(bars)
                if signal is None or len(signal) == 0:
                    continue
                if bool(signal.iloc[-1]):
                    result.n_candidates += 1
                    result.opportunities.append(
                        self._make_opportunity(symbol, strat, bars, as_of))
        result.opportunities = rank_opportunities(result.opportunities)
        result.duration_ms = (time.perf_counter() - started) * 1000.0
        logger.info("scan %s: %s", self.timeframe, result.summary())
        return result

    # --------------------------------------------------------------- helpers

    def _make_opportunity(self, symbol: str, strat: StrategyProfile,
                          bars: pd.DataFrame, as_of) -> Opportunity:
        confidence, extras = self.score_fn(symbol, strat, bars)
        direction = strat.meta.direction.value
        opp = Opportunity(
            symbol=symbol, strategy=strat.name, direction=direction,
            confidence=float(confidence),
            expected_reward=extras.get("expected_reward"),
            expected_risk=extras.get("expected_risk"),
            expected_holding_min=extras.get("expected_holding_min"),
            reason=extras.get("reason", f"{strat.name} entry on {self.timeframe}"))
        if self.evidence_logger is not None:
            self._record(symbol, strat, bars, as_of, opp)
        return opp

    def _record(self, symbol, strat, bars, as_of, opp: Opportunity) -> None:
        """Record the firing candidate as a signal (recorded_only). Best-effort:
        a missing instrument (FK) is logged, never aborts the scan."""
        try:
            last = bars.iloc[-1]
            self.evidence_logger.record_signal(Signal(
                ts=str(pd.Timestamp(last["date"])),
                symbol=symbol, strategy_id=self._strategy_ids[strat.name],
                direction=opp.direction, mode=self.mode,
                disposition=Disposition.RECORDED_ONLY.value,
                disposition_reason="scan candidate (no confidence/selection yet)",
                entry_price=float(last["close"]),
                confidence_score=opp.confidence))
        except Exception as exc:  # never let evidence logging break a scan
            logger.warning("signal not recorded for %s/%s: %s",
                           symbol, strat.name, exc)
