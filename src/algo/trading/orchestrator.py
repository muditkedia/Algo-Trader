"""Orchestrator - run every enabled strategy over the watchlist, once per bar.

Strategy-AGNOSTIC: it discovers strategies from the registry (the same
discovery the backtests use), groups them by timeframe, and for each symbol
calls the frozen ``prepare`` / ``entry_signal`` and builds a normalized
``TradingSignal`` via the shared level helpers. No strategy-specific code and
no per-strategy branches.

Duplicate-evaluation avoidance: a strategy/symbol/timeframe is evaluated at
most once per COMPLETED bar (keyed on the last bar's timestamp), so re-entrant
ticks within the same bar do no repeated work. Only signals on the newest
completed bar are emitted (a strategy that fired historically is not re-traded
on restart).

Data comes from :class:`~algo.marketdata.state.MarketState` and nowhere else.
The orchestrator cannot fetch, cannot reach a provider, and cannot tell how a
candle arrived - which is what lets the transport underneath be swapped from
REST to websocket to a replay engine without a line changing here.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from algo.core.indicators import atr as atr_series
from algo.core.logging import get_logger
from algo.risk.engine import RiskParams
from algo.trading.signals import TradingSignal, build_signal

logger = get_logger("trading.orchestrator")


class Orchestrator:
    def __init__(self, strategies, state, params: Optional[RiskParams] = None,
                 atr_period: int = 14, swing_window: int = 10) -> None:
        #: instantiated, enabled intraday strategy objects
        self.strategies = list(strategies)
        #: THE MarketState. Read-only from here.
        self.state = state
        self.params = params or RiskParams()
        self.atr_period = atr_period
        self.swing_window = swing_window
        #: (strategy, symbol, timeframe) -> last evaluated bar timestamp
        self._last_eval: Dict[tuple, pd.Timestamp] = {}
        self.diagnostics: List[dict] = []

    def timeframes(self) -> List[str]:
        return sorted({s.meta.timeframe for s in self.strategies})

    def _symbols(self, timeframe: str) -> List[str]:
        """The watchlist minus anything health has set aside."""
        picker = getattr(self.state, "usable_symbols", None)
        symbols = picker(timeframe) if picker is not None else self.state.symbols
        context = set(getattr(self.state, "context_symbols", ()))
        return [symbol for symbol in symbols if symbol not in context]

    def evaluate(self, timeframe: str) -> List[TradingSignal]:
        """One pass: every strategy on ``timeframe`` over the watchlist,
        emitting signals that fire on each symbol's newest completed bar.

        Scans the USABLE symbols only. A symbol the provider cannot serve is
        set aside individually, so three unreachable names out of ninety-nine
        cost three - the scan proceeds on the ninety-six that are fine rather
        than halting on a partial failure.
        """
        signals: List[TradingSignal] = []
        self.diagnostics = []
        strategies = [s for s in self.strategies
                      if s.meta.timeframe == timeframe]
        if not strategies:
            return signals
        raw = {symbol: self.state.history(symbol, timeframe)
               for symbol in self._symbols(timeframe)}
        raw = {symbol: frame for symbol, frame in raw.items()
               if not frame.empty}
        for strat in strategies:
            prepared = {}
            for symbol, frame in raw.items():
                if len(frame) < strat.min_history():
                    continue
                try:
                    prepared[symbol] = strat.prepare(frame)
                except Exception as exc:
                    logger.warning("prepare %s/%s failed: %s",
                                   strat.name, symbol, exc)
            context = {symbol: self.state.history(symbol, timeframe)
                       for symbol in strat.context_symbols}
            try:
                prepared = strat.prepare_context(prepared, context)
                if strat.cross_sectional:
                    prepared = strat.prepare_cross_section(prepared)
            except Exception as exc:
                logger.warning("context preparation %s failed: %s",
                               strat.name, exc)
                continue
            for symbol, frame in prepared.items():
                last_ts = pd.Timestamp(frame["date"].iloc[-1])
                key = (strat.name, symbol, timeframe)
                if self._last_eval.get(key) == last_ts:
                    continue                    # already evaluated this bar
                self._last_eval[key] = last_ts
                self.diagnostics.extend(strat.signal_diagnostics(frame, symbol))
                signals.extend(self._evaluate_prepared(
                    strat, frame, symbol, timeframe))

        # Resolve opposite-direction conflicts by confidence before portfolio
        # allocation, then honor the specification priority metric globally.
        best_direction = {}
        for sig in signals:
            existing = best_direction.get(sig.symbol)
            if (existing is not None and existing.direction != sig.direction
                    and existing.confidence >= sig.confidence):
                continue
            if existing is not None and existing.direction != sig.direction:
                best_direction[sig.symbol] = sig
            elif existing is None:
                best_direction[sig.symbol] = sig
        filtered = [s for s in signals
                    if (best_direction.get(s.symbol) is None
                        or best_direction[s.symbol].direction == s.direction)]
        return sorted(filtered,
                      key=lambda s: (s.priority_score, s.confidence),
                      reverse=True)

    def _evaluate_prepared(self, strat, prepared, symbol, timeframe):
        try:
            entries_by_direction = strat.entry_signals(prepared)
        except Exception as exc:
            logger.warning("evaluate %s/%s failed: %s",
                           strat.name, symbol, exc)
            return []
        idx = len(prepared) - 1
        # ATR + swing low with the backtest engine's own conventions
        work = prepared
        if "atr" in work.columns:
            atr_val = float(work["atr"].iloc[idx])
        else:
            atr_val = float(atr_series(work, self.atr_period).iloc[idx])
        swing_low = float(work["low"].rolling(
            self.swing_window, min_periods=1).min().iloc[idx])
        swing_high = float(work["high"].rolling(
            self.swing_window, min_periods=1).max().iloc[idx])
        out = []
        for direction, entries in entries_by_direction.items():
            if entries is None or not bool(entries.iloc[-1]):
                continue
            sig = build_signal(
                strat, prepared, idx, timeframe, params=self.params,
                atr_value=atr_val, swing_low=swing_low,
                swing_high=swing_high, direction=direction)
            if sig is not None:
                object.__setattr__(sig, "symbol", symbol)
                out.append(sig)
        return out
