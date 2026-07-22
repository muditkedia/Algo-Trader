"""ScanEngine - one unified scan across every eligible stock and strategy.

Each scan cycle processes EVERY eligible symbol: for each timeframe any enabled
strategy trades (strategies declare theirs in ``meta.timeframe``), it loads the
symbol's bars once (point-in-time, up to ``as_of``), runs each strategy's own
indicator preparation and vectorized entry signal, and builds the CANONICAL
``Opportunity`` for firing candidates - entry/stop prices from the promoted
risk engine, expected risk/reward, liquidity metrics, estimated round-trip
cost, regime label (via the injectable ``regime_fn``), historical evidence
stats, and the full confidence-component breakdown.

Ranking: when an ``OpportunityRanker`` is supplied, opportunities are ordered
by its configurable weighted score (evidence-based); otherwise by the plain
confidence fallback. EVERY ranking decision is persisted - each firing signal
is recorded once with its rank and per-component ranking breakdown (under the
``_ranking`` key of the signal's component JSON), and re-scans that re-observe
the same bar update the stored rank instead of duplicating the signal.

Scan scheduling: ``scan(..., timeframes=...)`` restricts a cycle to the
timeframe groups that are actually due (see scanner/scheduler.py), so 15m
strategies can be evaluated every 30-60s without re-running daily strategies.

Pluggable seams (unchanged): ``prepare_fn`` (global enrichment) and
``score_fn`` (confidence override - the future calibrated engine drops in
here without touching strategies).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import pandas as pd

from algo.core.costs import CostModel, NseEquityCostModel, Product
from algo.core.enums import Direction, HoldingScope
from algo.core.logging import get_logger
from algo.data.ohlcv import timeframe_minutes
from algo.evidence.models import Disposition, Mode, Signal
from algo.risk.engine import RiskParams, initial_stop_pct
from algo.scanner.base import Opportunity, Scanner, rank_opportunities
from algo.strategies.base import StrategyProfile
from algo.trading.signals import build_signal

logger = get_logger("scanner.engine")

#: Reference notional for the per-opportunity cost estimate.
COST_REF_STAKE = 100_000.0
#: Forward window (bars) used for the expected-holding estimate - matches the
#: research engine's default simulation horizon.
EXPECTED_HOLD_BARS = 8


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
    """Delegate to the strategy's own component confidence."""
    result = strategy.confidence(bars)
    return result.score, {"components": result.components,
                          "reason": result.reason}


class ScanEngine(Scanner):
    def __init__(self, store, strategies: List[StrategyProfile],
                 timeframe: str = "1d", lookback_bars: int = 200,
                 evidence_logger=None, mode: str = Mode.BACKTEST.value,
                 prepare_fn: Optional[Callable] = None,
                 score_fn: Optional[Callable] = None,
                 ranker=None, risk_params: Optional[RiskParams] = None,
                 cost_model: Optional[CostModel] = None,
                 regime_fn: Optional[Callable] = None) -> None:
        self.store = store
        self.strategies = [s for s in strategies if s.enabled]
        self.timeframe = timeframe          # fallback / no-strategy walk
        self.lookback_bars = lookback_bars
        self.evidence_logger = evidence_logger
        self.mode = mode
        self.prepare_fn = prepare_fn or (lambda bars: bars)
        self.score_fn = score_fn or _default_score
        self.ranker = ranker
        self.risk_params = risk_params or RiskParams()
        self.cost_model = cost_model or NseEquityCostModel()
        self.regime_fn = regime_fn
        # Map strategy name -> evidence strategy_id (idempotent registration).
        self._strategy_ids: Dict[str, int] = {}
        if self.evidence_logger is not None:
            for strat in self.strategies:
                self._strategy_ids[strat.name] = \
                    self.evidence_logger.register_strategy(
                        strat.name, strat.meta.version)

    # --------------------------------------------------------------- grouping

    def _by_timeframe(self) -> Dict[str, List[StrategyProfile]]:
        groups: Dict[str, List[StrategyProfile]] = {}
        for strat in self.strategies:
            tf = getattr(strat.meta, "timeframe", None) or self.timeframe
            groups.setdefault(tf, []).append(strat)
        return groups or {self.timeframe: []}

    def timeframes(self) -> List[str]:
        """Timeframes any enabled strategy trades on (for the scheduler)."""
        return sorted(self._by_timeframe())

    # ------------------------------------------------------------------ scan

    def scan(self, as_of, symbols: Optional[List[str]] = None,
             timeframes: Optional[List[str]] = None) -> ScanResult:
        symbols = list(symbols or [])
        groups = self._by_timeframe()
        if timeframes is not None:
            groups = {tf: strats for tf, strats in groups.items()
                      if tf in set(timeframes)} or {}
        result = ScanResult(as_of=as_of,
                            timeframe=",".join(sorted(groups)) or "-",
                            n_symbols_requested=len(symbols))
        started = time.perf_counter()
        if self.ranker is not None:
            self.ranker.refresh_stats()
        symbols_with_data = set()

        candidates: List[tuple] = []        # (opportunity, strategy, prepared)
        for tf, strats in groups.items():
            need = max([self.lookback_bars]
                       + [s.min_history() for s in strats])
            raw = {}
            for symbol in symbols:
                bars = self.store.read(symbol, tf, end=as_of)
                if bars.empty:
                    continue
                symbols_with_data.add(symbol)
                raw[symbol] = self.prepare_fn(
                    bars.tail(need).reset_index(drop=True))
            for strat in strats:
                prepared_frames = {
                    symbol: strat.prepare(frame) for symbol, frame in raw.items()}
                context = {
                    context_symbol: self.store.read(
                        context_symbol, tf, end=as_of).tail(need).reset_index(drop=True)
                    for context_symbol in strat.context_symbols}
                prepared_frames = strat.prepare_context(prepared_frames, context)
                if strat.cross_sectional:
                    prepared_frames = strat.prepare_cross_section(prepared_frames)
                for symbol, prepared in prepared_frames.items():
                    signals = strat.entry_signals(prepared)
                    for direction, signal in signals.items():
                        if signal is None or len(signal) == 0 \
                                or not bool(signal.iloc[-1]):
                            continue
                        opp = self._make_opportunity(
                            symbol, strat, prepared, tf, direction)
                        if opp is None:
                            continue
                        result.n_candidates += 1
                        candidates.append((opp, strat, prepared))

        # ---- rank (configurable weighted engine, else confidence fallback)
        opportunities = [c[0] for c in candidates]
        if self.ranker is not None:
            opportunities = self.ranker.rank(opportunities)
        else:
            opportunities = rank_opportunities(opportunities)
        result.opportunities = opportunities

        # ---- persist every ranking decision to evidence
        if self.evidence_logger is not None:
            by_id = {id(c[0]): c for c in candidates}
            for opp in opportunities:
                _, strat, prepared = by_id[id(opp)]
                self._record(opp, strat, prepared)

        result.n_symbols_with_data = len(symbols_with_data)
        result.duration_ms = (time.perf_counter() - started) * 1000.0
        logger.info("scan: %s", result.summary())
        return result

    # ------------------------------------------------------ opportunity build

    def _make_opportunity(self, symbol: str, strat: StrategyProfile,
                          bars: pd.DataFrame, timeframe: str,
                          direction: Direction = Direction.LONG
                          ) -> Optional[Opportunity]:
        if self.score_fn is _default_score:
            scored = strat.confidence_for(bars, direction)
            confidence = scored.score
            extras = {"components": scored.components,
                      "reason": scored.reason}
        else:
            confidence, extras = self.score_fn(symbol, strat, bars)
        last = bars.iloc[-1]
        entry = float(last["close"])

        atr = (float(last["atr"]) if "atr" in bars.columns
               and pd.notna(last.get("atr")) else None)
        swing_low = float(bars["low"].tail(10).min()) if len(bars) else None
        swing_high = float(bars["high"].tail(10).max()) if len(bars) else None
        if strat.execution is None:  # lightweight research/test profiles
            stop_pct = initial_stop_pct(
                entry, atr, swing_low, self.risk_params)
            if stop_pct is not None:
                stop_pct = min(stop_pct, self.risk_params.hard_stop_pct)
            stop_price = (entry * (1 - stop_pct)) if stop_pct else None
            reward_pct = (self.risk_params.reward_atr_multiple * atr / entry
                          if atr else None)
        else:
            normalized = build_signal(
                strat, bars, len(bars) - 1, timeframe, params=self.risk_params,
                atr_value=atr, swing_low=swing_low, swing_high=swing_high,
                direction=direction)
            if normalized is None:
                return None
            stop_price = normalized.stop
            stop_pct = abs(entry - normalized.stop) / entry
            reward_pct = (abs(normalized.target - entry) / entry
                          if normalized.target is not None else None)
        risk_reward = (reward_pct / stop_pct
                       if reward_pct and stop_pct else None)

        product = (Product.INTRADAY
                   if strat.meta.holding_scope == HoldingScope.INTRADAY
                   else Product.DELIVERY)
        est_cost = self.cost_model.round_trip_pct(
            entry_price=entry, exit_price=entry,
            quantity=max(COST_REF_STAKE / entry, 1e-9), product=product)

        liquidity = {}
        if "volume_ratio" in bars.columns and pd.notna(last.get("volume_ratio")):
            liquidity["volume_ratio"] = float(last["volume_ratio"])
        if pd.notna(last.get("volume")):
            liquidity["traded_value"] = float(last["volume"]) * entry

        stats = self.ranker.stats_for(strat.name) if self.ranker else {}
        regime = self.regime_fn(symbol) if self.regime_fn else None

        return Opportunity(
            symbol=symbol, strategy=strat.name,
            direction=direction.value,
            confidence=float(confidence),
            timeframe=timeframe,
            signal_ts=str(pd.Timestamp(last["date"])),
            entry_price=entry,
            stop_price=stop_price,
            expected_risk=stop_pct,
            expected_reward=reward_pct,
            risk_reward=risk_reward,
            expected_return=stats.get("expectancy"),
            expected_holding_min=(timeframe_minutes(timeframe)
                                  * EXPECTED_HOLD_BARS),
            hist_win_rate=stats.get("win_rate"),
            hist_expectancy=stats.get("expectancy"),
            hist_trades=stats.get("n_trades"),
            confidence_components=extras.get("components") or {},
            regime=regime,
            liquidity=liquidity,
            est_cost_pct=est_cost,
            reason=extras.get("reason")
            or f"{strat.name} entry on {timeframe}")

    # --------------------------------------------------------------- persist

    def _record(self, opp: Opportunity, strat: StrategyProfile,
                bars: pd.DataFrame) -> None:
        """Record the candidate + its ranking decision. Duplicate-safe: a
        re-observed bar updates the stored rank instead of inserting again.
        Best-effort - evidence failures never abort a scan."""
        try:
            strategy_id = self._strategy_ids[strat.name]
            existing = self.evidence_logger.find_signal(
                strategy_id, opp.symbol, opp.signal_ts, self.mode)
            if existing is not None:
                opp.signal_id = existing
                if opp.rank is not None:
                    self.evidence_logger.update_rank(existing, opp.rank)
                return
            components = dict(opp.confidence_components or {})
            components["_ranking"] = {"score": opp.rank_score,
                                      "breakdown": opp.rank_breakdown}
            opp.signal_id = self.evidence_logger.record_signal(Signal(
                ts=opp.signal_ts, symbol=opp.symbol, strategy_id=strategy_id,
                direction=opp.direction, mode=self.mode,
                disposition=Disposition.RECORDED_ONLY.value,
                disposition_reason="scan candidate",
                entry_price=opp.entry_price,
                atr_pct=(opp.expected_risk / self.risk_params.atr_stop_multiplier
                         if opp.expected_risk else None),
                relative_volume=opp.liquidity.get("volume_ratio"),
                traded_value=opp.liquidity.get("traded_value"),
                trend_regime=opp.regime,
                confidence_score=opp.confidence,
                confidence_components=components,
                expected_reward_pct=opp.expected_reward,
                expected_risk_pct=opp.expected_risk,
                expected_holding_min=opp.expected_holding_min,
                expected_value_net=opp.expected_return,
                rank_in_scan=opp.rank))
        except Exception as exc:  # never let evidence logging break a scan
            logger.warning("signal not recorded for %s/%s: %s",
                           opp.symbol, strat.name, exc)
