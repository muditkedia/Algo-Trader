"""Trading-calendar interface (framework only; no NSE holiday rules yet).

The platform needs one place to answer "is this a trading session?", "what are
today's session bounds?", and "what is the previous/next session?". Everything
downstream (data continuity checks, warmup counting, session square-off,
research forward-return horizons) reads sessions from here rather than assuming
a 24/7 clock - the single biggest crypto assumption being removed.

Phase 1 delivers the ``TradingCalendar`` interface plus ``StaticCalendar``, a
data-driven implementation that is handed an explicit set of session dates and
fixed session times. It encodes NO holiday logic - the concrete NSE calendar
(holiday list, muhurat/short sessions) is a Phase-2 data task. StaticCalendar
is enough to unit-test everything that depends on the interface without market
data.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, time
from typing import Iterable, List, Optional


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


class TradingCalendar(ABC):
    """Session calendar contract. Implementations decide which days trade."""

    @abstractmethod
    def is_session(self, day) -> bool:
        """True if ``day`` (date/datetime/ISO str) is a trading session."""

    @abstractmethod
    def sessions(self, start, end) -> List[date]:
        """Ordered trading-session dates in the inclusive ``[start, end]`` range."""

    @abstractmethod
    def session_bounds(self, day) -> Optional[tuple]:
        """(open_dt, close_dt) naive-local datetimes for ``day``, or None."""

    def previous_session(self, day) -> Optional[date]:
        d = _as_date(day)
        found = [s for s in self.sessions(date(d.year - 2, 1, 1), d) if s < d]
        return found[-1] if found else None

    def next_session(self, day) -> Optional[date]:
        d = _as_date(day)
        found = [s for s in self.sessions(d, date(d.year + 2, 1, 1)) if s > d]
        return found[0] if found else None


class StaticCalendar(TradingCalendar):
    """Calendar backed by an explicit set of session dates + fixed times.

    No rules, no market knowledge - it simply trusts the dates it is given.
    Useful for tests and for driving the platform from a materialized session
    list (e.g. one derived from bhavcopy dates in Phase 2).
    """

    def __init__(
        self,
        session_dates: Iterable,
        open_time: time = time(9, 15),
        close_time: time = time(15, 30),
    ) -> None:
        self._sessions = sorted({_as_date(d) for d in session_dates})
        self._open = open_time
        self._close = close_time

    def is_session(self, day) -> bool:
        return _as_date(day) in set(self._sessions)

    def sessions(self, start, end) -> List[date]:
        lo, hi = _as_date(start), _as_date(end)
        return [d for d in self._sessions if lo <= d <= hi]

    def session_bounds(self, day) -> Optional[tuple]:
        d = _as_date(day)
        if d not in set(self._sessions):
            return None
        return (datetime.combine(d, self._open),
                datetime.combine(d, self._close))
