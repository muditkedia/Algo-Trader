"""Market-data freshness - how old is the price we are about to act on?

The first paper session displayed prices that were hours old while the
dashboard reported the data as current. The cause was a category error, not a
bug in any one place: ``generated_at`` on every snapshot is the time the
EXPORTER ran, and the UI compared that to the wall clock. It therefore measured
the exporter's liveness and displayed it as the data's freshness. A trace
measured a 4,261-minute-old candle being reported as "0.0s" old.

This module supplies the missing quantity: for each symbol, how far its newest
stored bar lags the bar the exchange should have produced by now. Everything
downstream - the mark price, the health beat, the dashboard - reads THIS, so
nothing has to infer age from an export timestamp again.

Staleness is measured in BARS, not seconds, because that is the unit the
pipeline actually works in: one bar behind immediately after a bar closes is
normal (the scheduler waits ``bar_grace_seconds`` for the feed), while three
bars behind at the same moment is a fault. Seconds are reported too, for
display, but the verdict is bar-based so it does not fire spuriously at every
bar boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from algo.core.logging import get_logger

logger = get_logger("trading.freshness")

#: A symbol may be this many bars behind the expected bar without being called
#: stale. One bar absorbs the normal fetch window (bar close -> grace -> fetch);
#: anything beyond it means the feed is genuinely not keeping up.
DEFAULT_TOLERANCE_BARS = 1

#: Status vocabulary. The operator reads these directly.
FRESH = "FRESH"
STALE = "STALE"
MISSING = "MISSING"          # no bars at all for this symbol
CLOSED = "MARKET CLOSED"     # no bar is DUE - absence is not staleness


def timeframe_minutes(timeframe: str) -> int:
    unit, n = timeframe[-1], int(timeframe[:-1])
    return n * (60 if unit == "h" else 1)


@dataclass
class SymbolFreshness:
    """One symbol's data age, and whether that age is acceptable right now."""

    symbol: str
    last_bar: Optional[pd.Timestamp] = None      # UTC, bar OPEN time
    age_seconds: Optional[float] = None
    bars_behind: Optional[int] = None
    status: str = MISSING
    reason: str = ""

    @property
    def fresh(self) -> bool:
        return self.status in (FRESH, CLOSED)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "last_bar": None if self.last_bar is None else str(self.last_bar),
            "age_seconds": (None if self.age_seconds is None
                            else round(self.age_seconds, 1)),
            "bars_behind": self.bars_behind,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass
class FreshnessReport:
    """Watchlist-wide data quality for one timeframe (dashboard §12 panel).

    Deliberately carries counts AND the per-symbol detail: the counts are what
    an operator scans, the detail is what they need the moment a count is wrong.
    """

    timeframe: str
    market_open: bool = False
    expected_bar: Optional[pd.Timestamp] = None
    symbols: List[SymbolFreshness] = field(default_factory=list)
    tolerance_bars: int = DEFAULT_TOLERANCE_BARS

    # ------------------------------------------------------------- counts

    @property
    def total(self) -> int:
        return len(self.symbols)

    @property
    def fresh(self) -> List[SymbolFreshness]:
        return [s for s in self.symbols if s.status == FRESH]

    @property
    def stale(self) -> List[SymbolFreshness]:
        return [s for s in self.symbols if s.status == STALE]

    @property
    def missing(self) -> List[SymbolFreshness]:
        return [s for s in self.symbols if s.status == MISSING]

    @property
    def live(self) -> List[SymbolFreshness]:
        """Symbols that have ANY data (fresh, stale or closed-market)."""
        return [s for s in self.symbols if s.status != MISSING]

    @property
    def newest_bar(self) -> Optional[pd.Timestamp]:
        bars = [s.last_bar for s in self.symbols if s.last_bar is not None]
        return max(bars) if bars else None

    @property
    def worst_bars_behind(self) -> int:
        behind = [s.bars_behind for s in self.symbols
                  if s.bars_behind is not None]
        return max(behind) if behind else 0

    # ------------------------------------------------------------- verdict

    @property
    def status(self) -> str:
        """One word for the whole watchlist, worst-case."""
        if not self.symbols:
            return MISSING
        if self.missing and len(self.missing) == self.total:
            return MISSING
        if self.stale:
            return STALE
        if not self.market_open:
            return CLOSED
        return FRESH

    @property
    def ok(self) -> bool:
        return self.status in (FRESH, CLOSED)

    def headline(self, examples: int = 5) -> str:
        if not self.symbols:
            return f"{self.timeframe}: no symbols on the watchlist"
        if self.status == MISSING:
            return (f"{self.timeframe}: NO DATA for any of {self.total} "
                    f"symbols")
        if self.status == STALE:
            names = [s.symbol for s in self.stale[:examples]]
            more = (f", +{len(self.stale) - examples} more"
                    if len(self.stale) > examples else "")
            return (f"{self.timeframe}: STALE DATA - {len(self.stale)}/"
                    f"{self.total} symbols are {self.worst_bars_behind} or more "
                    f"bars behind (examples: {', '.join(names)}{more})")
        if self.status == CLOSED:
            newest = self.newest_bar
            return (f"{self.timeframe}: market closed - showing the last "
                    f"session's candles"
                    + (f" (newest {newest})" if newest is not None else ""))
        return (f"{self.timeframe}: {len(self.fresh)}/{self.total} symbols "
                f"current")

    def to_dict(self, detail_limit: int = 50) -> dict:
        stale = self.stale
        return {
            "timeframe": self.timeframe,
            "status": self.status,
            "ok": self.ok,
            "market_open": self.market_open,
            "headline": self.headline(),
            "expected_bar": (None if self.expected_bar is None
                             else str(self.expected_bar)),
            "newest_bar": (None if self.newest_bar is None
                           else str(self.newest_bar)),
            "total": self.total,
            "fresh": len(self.fresh),
            "stale": len(stale),
            "missing": len(self.missing),
            "live": len(self.live),
            "worst_bars_behind": self.worst_bars_behind,
            "tolerance_bars": self.tolerance_bars,
            "stale_symbols": [s.symbol for s in stale],
            "missing_symbols": [s.symbol for s in self.missing],
            "examples": [s.to_dict() for s in stale[:detail_limit]],
        }


def assess(symbol: str, last_bar: Optional[pd.Timestamp],
           expected_bar: Optional[pd.Timestamp], timeframe: str,
           now: pd.Timestamp,
           tolerance_bars: int = DEFAULT_TOLERANCE_BARS) -> SymbolFreshness:
    """Freshness verdict for one symbol.

    ``expected_bar`` is the open time of the newest bar the exchange should
    have completed by ``now`` (None when the market is shut, in which case an
    old bar is CORRECT rather than stale - refusing to conflate those is the
    whole point).
    """
    out = SymbolFreshness(symbol=symbol, last_bar=last_bar)
    if last_bar is None:
        out.status = MISSING
        out.reason = "no candles stored for this symbol"
        return out

    out.age_seconds = max(0.0, (now - last_bar).total_seconds())
    if expected_bar is None:
        out.status = CLOSED
        out.reason = "no bar is due - the market is not producing candles"
        return out

    step_min = timeframe_minutes(timeframe)
    delta_min = (expected_bar - last_bar).total_seconds() / 60.0
    out.bars_behind = max(0, int(round(delta_min / step_min)))
    if out.bars_behind <= tolerance_bars:
        out.status = FRESH
        out.reason = ("current" if not out.bars_behind
                      else f"{out.bars_behind} bar behind (within the "
                           f"{tolerance_bars}-bar fetch window)")
        return out
    out.status = STALE
    minutes = out.bars_behind * step_min
    out.reason = (f"{out.bars_behind} bars ({minutes} min) behind the expected "
                  f"{timeframe} bar at {expected_bar}")
    return out


def build_report(symbols, last_bars: Dict[str, Optional[pd.Timestamp]],
                 timeframe: str, expected_bar: Optional[pd.Timestamp],
                 market_open: bool, now: Optional[pd.Timestamp] = None,
                 tolerance_bars: int = DEFAULT_TOLERANCE_BARS
                 ) -> FreshnessReport:
    """Assemble the watchlist report from each symbol's newest stored bar."""
    now = now or pd.Timestamp.now(tz="UTC")
    report = FreshnessReport(timeframe=timeframe, market_open=market_open,
                             expected_bar=expected_bar,
                             tolerance_bars=tolerance_bars)
    for symbol in symbols:
        report.symbols.append(
            assess(symbol, last_bars.get(symbol), expected_bar, timeframe,
                   now, tolerance_bars))
    return report
