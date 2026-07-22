"""Adaptive scan scheduler - evaluate each timeframe at its own cadence.

A 15m strategy deserves a 30-60s evaluation cadence; re-running a daily
strategy that often is pure waste. The scheduler tracks when each timeframe
group was last evaluated and answers "which timeframes are due NOW", so the
paper loop can tick frequently (managing positions every tick) while scanning
each strategy group only as often as its bars can actually change.

Cadences are configuration, not code (``ScanCadence.from_dict``); unknown
timeframes fall back to a conservative default. ``force()`` marks everything
due (used at startup and after data refreshes).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from algo.core.config import from_dict


@dataclass(frozen=True)
class ScanCadence:
    """Seconds between evaluations, per strategy timeframe."""

    tf_15m: float = 45.0       # 15m strategies: 30-60s band
    tf_1h: float = 180.0       # 1h strategies: 2-5 min band
    tf_1d: float = 420.0       # daily strategies: 5-10 min band
    default: float = 300.0     # anything else

    _MAP = {"15m": "tf_15m", "1h": "tf_1h", "1d": "tf_1d"}

    def seconds_for(self, timeframe: str) -> float:
        return float(getattr(self, self._MAP.get(timeframe, ""), self.default))

    @classmethod
    def from_dict(cls, data) -> "ScanCadence":
        return from_dict(cls, data)


class ScanScheduler:
    """Decides which timeframes need evaluation at a given moment."""

    def __init__(self, timeframes: List[str],
                 cadence: Optional[ScanCadence] = None,
                 clock=None) -> None:
        self.cadence = cadence or ScanCadence()
        self.clock = clock or time.monotonic
        self._last: Dict[str, Optional[float]] = {tf: None for tf in timeframes}

    def due(self, now: Optional[float] = None) -> List[str]:
        """Timeframes whose cadence has elapsed (never-scanned = always due)."""
        now = self.clock() if now is None else now
        out = []
        for tf, last in self._last.items():
            if last is None or (now - last) >= self.cadence.seconds_for(tf):
                out.append(tf)
        return sorted(out)

    def mark_scanned(self, timeframes: List[str],
                     now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        for tf in timeframes:
            if tf in self._last:
                self._last[tf] = now

    def force(self) -> None:
        """Make every timeframe due on the next check."""
        for tf in self._last:
            self._last[tf] = None

    def next_due_in(self, now: Optional[float] = None) -> float:
        """Seconds until the soonest timeframe becomes due (0 if any due now)."""
        now = self.clock() if now is None else now
        waits = []
        for tf, last in self._last.items():
            if last is None:
                return 0.0
            remaining = self.cadence.seconds_for(tf) - (now - last)
            waits.append(max(0.0, remaining))
        return min(waits) if waits else 0.0
