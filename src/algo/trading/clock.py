"""MarketClock - session management for the production loop (single source
of market-time truth: IST wall-clock, session bounds, bar boundaries,
square-off and entry-cutoff times, holiday awareness).

Sessions default to NSE weekdays 09:15-15:30 IST minus an optional holiday
file (one ISO date per line, maintained from the exchange calendar). Special
sessions (Muhurat) are NOT auto-traded: a day must be a normal session for
the clock to open - the conservative production stance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional, Set
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
OPEN_T = time(9, 15)
CLOSE_T = time(15, 30)


def _tf_minutes(timeframe: str) -> int:
    unit = timeframe[-1]
    n = int(timeframe[:-1])
    return n * (60 if unit == "h" else 1)


@dataclass
class MarketClock:
    holidays: Set[date]
    squareoff: time = time(15, 15)
    entry_cutoff: time = time(15, 0)

    @classmethod
    def build(cls, holiday_file: Optional[str] = None,
              squareoff: time = time(15, 15),
              entry_cutoff: time = time(15, 0)) -> "MarketClock":
        holidays: Set[date] = set()
        if holiday_file and Path(holiday_file).exists():
            for line in Path(holiday_file).read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    holidays.add(date.fromisoformat(line))
        return cls(holidays=holidays, squareoff=squareoff,
                   entry_cutoff=entry_cutoff)

    # ------------------------------------------------------------ sessions

    def now(self) -> datetime:
        return datetime.now(tz=IST)

    def is_session_day(self, d: Optional[date] = None) -> bool:
        d = d or self.now().date()
        return d.weekday() < 5 and d not in self.holidays

    def session_open(self, d: Optional[date] = None) -> datetime:
        d = d or self.now().date()
        return datetime.combine(d, OPEN_T, tzinfo=IST)

    def session_close(self, d: Optional[date] = None) -> datetime:
        d = d or self.now().date()
        return datetime.combine(d, CLOSE_T, tzinfo=IST)

    def is_open(self, at: Optional[datetime] = None) -> bool:
        at = at or self.now()
        if not self.is_session_day(at.date()):
            return False
        return self.session_open(at.date()) <= at < self.session_close(at.date())

    def past_squareoff(self, at: Optional[datetime] = None) -> bool:
        at = at or self.now()
        return at.timetz().replace(tzinfo=None) >= self.squareoff

    def past_entry_cutoff(self, at: Optional[datetime] = None) -> bool:
        at = at or self.now()
        return at.timetz().replace(tzinfo=None) >= self.entry_cutoff

    # ------------------------------------------------------- bar boundaries

    def last_closed_bar_open(self, timeframe: str,
                             at: Optional[datetime] = None
                             ) -> Optional[datetime]:
        """Open time (IST) of the most recent COMPLETED bar of ``timeframe``
        in the current session, or None before the first bar completes."""
        at = at or self.now()
        if not self.is_session_day(at.date()):
            return None
        start = self.session_open(at.date())
        step = timedelta(minutes=_tf_minutes(timeframe))
        if at < start + step:
            return None
        end = min(at, self.session_close(at.date()))
        n = int((end - start) // step)
        return start + step * (n - 1)

    def next_bar_close(self, timeframe: str,
                       at: Optional[datetime] = None) -> datetime:
        """When the currently-forming bar of ``timeframe`` completes."""
        at = at or self.now()
        start = self.session_open(at.date())
        step = timedelta(minutes=_tf_minutes(timeframe))
        if at <= start:
            return start + step
        n = int((at - start) // step)
        return start + step * (n + 1)
