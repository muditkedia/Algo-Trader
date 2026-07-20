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

    def timeframes(self) -> List[str]:
        return sorted({s.meta.timeframe for s in self.strategies})

    def _symbols(self, timeframe: str) -> List[str]:
        """The watchlist minus anything health has set aside."""
        picker = getattr(self.state, "usable_symbols", None)
        return picker(timeframe) if picker is not None else self.state.symbols

    def evaluate(self, timeframe: str) -> List[TradingSignal]:
        """One pass: every strategy on ``timeframe`` over the watchlist,
        emitting signals that fire on each symbol's newest completed bar.

        Scans the USABLE symbols only. A symbol the provider cannot serve is
        set aside individually, so three unreachable names out of ninety-nine
        cost three - the scan proceeds on the ninety-six that are fine rather
        than halting on a partial failure.
        """
        signals: List[TradingSignal] = []
        strategies = [s for s in self.strategies
                      if s.meta.timeframe == timeframe]
        if not strategies:
            return signals
        for symbol in self._symbols(timeframe):
            frame = self.state.history(symbol, timeframe)
            if frame.empty:
                continue
            last_ts = pd.Timestamp(frame["date"].iloc[-1])
            for strat in strategies:
                key = (strat.name, symbol, timeframe)
                if self._last_eval.get(key) == last_ts:
                    continue                    # already evaluated this bar
                self._last_eval[key] = last_ts
                sig = self._evaluate_one(strat, frame, symbol, timeframe,
                                         last_ts)
                if sig is not None:
                    signals.append(sig)
        return signals

    def _evaluate_one(self, strat, frame, symbol, timeframe, last_ts):
        if len(frame) < strat.min_history():
            return None
        try:
            prepared = strat.prepare(frame)
            entries = strat.entry_signal(prepared)
        except Exception as exc:
            logger.warning("evaluate %s/%s failed: %s",
                           strat.name, symbol, exc)
            return None
        if entries is None or not bool(entries.iloc[-1]):
            return None                          # only the NEWEST bar fires
        idx = len(prepared) - 1
        # ATR + swing low with the backtest engine's own conventions
        work = prepared
        if "atr" in work.columns:
            atr_val = float(work["atr"].iloc[idx])
        else:
            atr_val = float(atr_series(work, self.atr_period).iloc[idx])
        swing_low = float(work["low"].rolling(
            self.swing_window, min_periods=1).min().iloc[idx])
        sig = build_signal(strat, prepared, idx, timeframe, params=self.params,
                           atr_value=atr_val, swing_low=swing_low)
        if sig is not None:
            # stamp the symbol (prepared frames may not carry it)
            object.__setattr__(sig, "symbol", symbol)
        return sig
