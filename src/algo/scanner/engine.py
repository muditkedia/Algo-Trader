"""ScanEngine - one unified scan across every eligible stock and strategy.

Each scan cycle processes EVERY eligible symbol: for each timeframe any enabled
strategy trades (strategies declare theirs in ``meta.timeframe``), it loads the
symbol's bars once (point-in-time, up to ``as_of``), runs each strategy's own
indicator preparation and vectorized entry signal, scores firing candidates via
the strategy's component confidence, and merges everything into ONE ranked
``Opportunity`` list.

Evidence: every firing candidate is recorded to the evidence store with its
full confidence-component breakdown. A duplicate guard
(``EvidenceLogger.signal_exists``) makes re-scanning the same bar idempotent -
scan cycles can overlap without double-counting signals.

Pluggable seams (unchanged from Phase 2):
  * ``prepare_fn(bars) -> bars``  - GLOBAL enrichment applied before strategies.
  * ``score_fn(symbol, strategy, bars) -> (confidence, extras)`` - scoring
    override; the default delegates to ``strategy.confidence`` (Phase 4), and
    the future evidence-calibrated confidence engine replaces it here without
    touching strategies.
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
    """Delegate to the strategy's own component confidence (Phase 4).

    The evidence-calibrated confidence engine later replaces this seam."""
    result = strategy.confidence(bars)
    return result.score, {"components": result.components,
                          "reason": result.reason}


class ScanEngine(Scanner):
    def __init__(self, store, strategies: List[StrategyProfile],
                 timeframe: str = "1d", lookback_bars: int = 200,
                 evidence_logger=None, mode: str = Mode.BACKTEST.value,
                 prepare_fn: Optional[Callable] = None,
                 score_fn: Optional[Callable] = None) -> None:
        self.store = store
        self.strategies = [s for s in strategies if s.enabled]
        self.timeframe = timeframe          # fallback / no-strategy walk
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

    # --------------------------------------------------------------- grouping

    def _by_timeframe(self) -> Dict[str, List[StrategyProfile]]:
        """Strategies grouped by the timeframe they trade on; when there are
        no strategies, the engine still walks the universe on its own
        timeframe (data-coverage accounting keeps working)."""
        groups: Dict[str, List[StrategyProfile]] = {}
        for strat in self.strategies:
            tf = getattr(strat.meta, "timeframe", None) or self.timeframe
            groups.setdefault(tf, []).append(strat)
        return groups or {self.timeframe: []}

    # ------------------------------------------------------------------ scan

    def scan(self, as_of, symbols: Optional[List[str]] = None) -> ScanResult:
        symbols = list(symbols or [])
        groups = self._by_timeframe()
        result = ScanResult(as_of=as_of, timeframe=",".join(sorted(groups)),
                            n_symbols_requested=len(symbols))
        started = time.perf_counter()
        symbols_with_data = set()

        for symbol in symbols:
            for tf, strats in groups.items():
                bars = self.store.read(symbol, tf, end=as_of)
                if bars.empty:
                    continue
                symbols_with_data.add(symbol)
                need = max([self.lookback_bars]
                           + [s.min_history() for s in strats])
                window = self.prepare_fn(
                    bars.tail(need).reset_index(drop=True))
                for strat in strats:
                    prepared = strat.prepare(window)
                    signal = strat.entry_signal(prepared)
                    if signal is None or len(signal) == 0:
                        continue
                    if bool(signal.iloc[-1]):
                        result.n_candidates += 1
                        result.opportunities.append(self._make_opportunity(
                            symbol, strat, prepared, tf))

        result.n_symbols_with_data = len(symbols_with_data)
        result.opportunities = rank_opportunities(result.opportunities)
        result.duration_ms = (time.perf_counter() - started) * 1000.0
        logger.info("scan: %s", result.summary())
        return result

    # --------------------------------------------------------------- helpers

    def _make_opportunity(self, symbol: str, strat: StrategyProfile,
                          bars: pd.DataFrame, timeframe: str) -> Opportunity:
        confidence, extras = self.score_fn(symbol, strat, bars)
        opp = Opportunity(
            symbol=symbol, strategy=strat.name,
            direction=strat.meta.direction.value,
            confidence=float(confidence),
            expected_reward=extras.get("expected_reward"),
            expected_risk=extras.get("expected_risk"),
            expected_holding_min=extras.get("expected_holding_min"),
            reason=extras.get("reason")
            or f"{strat.name} entry on {timeframe}")
        if self.evidence_logger is not None:
            self._record(symbol, strat, bars, opp, extras)
        return opp

    def _record(self, symbol, strat, bars, opp: Opportunity, extras) -> None:
        """Record the firing candidate as a signal with its confidence
        components. Duplicate-safe (same strategy/symbol/bar/mode recorded
        once) and best-effort: recording failures never abort a scan."""
        try:
            last = bars.iloc[-1]
            ts = str(pd.Timestamp(last["date"]))
            strategy_id = self._strategy_ids[strat.name]
            if self.evidence_logger.signal_exists(strategy_id, symbol, ts,
                                                  self.mode):
                return
            self.evidence_logger.record_signal(Signal(
                ts=ts, symbol=symbol, strategy_id=strategy_id,
                direction=opp.direction, mode=self.mode,
                disposition=Disposition.RECORDED_ONLY.value,
                disposition_reason="scan candidate (no selection layer yet)",
                entry_price=float(last["close"]),
                confidence_score=opp.confidence,
                confidence_components=extras.get("components") or None))
        except Exception as exc:  # never let evidence logging break a scan
            logger.warning("signal not recorded for %s/%s: %s",
                           symbol, strat.name, exc)
