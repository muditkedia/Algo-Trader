"""AdaptiveRateLimiter - a request budget the scheduler can plan against.

This replaces "sleep a fixed interval, and back off when rejected". That
pattern has three faults which only appear under load:

1. **It cannot see a longer window.** A fixed 1 req/s sleep respects 3/s and
   still walks into the 5,000/hour cap around the fourth hour of a session,
   because nothing was counting hours.
2. **It sleeps inside the transport**, so the caller cannot ask "may I send
   now?" and do something else if not. Scheduling and waiting become the same
   act, and priority becomes impossible - a blocked low-priority request holds
   the thread that a due one needs.
3. **Its backoff is amnesiac.** Every rejection is met with the same first
   step, so a provider that is persistently unhappy is rediscovered from
   scratch each time.

The replacement is a passive budget:

* **Multi-window.** Every declared :class:`RateLimit` is enforced at once via
  sliding windows, so per-second, per-minute and per-hour all bind.
* **It never sleeps.** :meth:`next_available` answers *when*, and the caller
  decides what to do with the interval. That makes the whole thing a pure
  function of recorded timestamps - deterministic, and testable without a
  clock.
* **AIMD.** A rejection multiplies an extra spacing penalty; sustained success
  decays it back toward the provider's floor. The limiter therefore converges
  on the rate the provider is ACTUALLY enforcing, which is the point: the
  documented 3/s was observed rejecting bulk downloads at 2.5/s (D-020), so
  the true limit is not a number we can read anywhere.

Time is passed in as a monotonic float. Nothing here reads a clock, so a test
can drive an hour of traffic in a millisecond.
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Dict, Iterable, List, Optional, Tuple

from algo.core.logging import get_logger
from algo.marketdata.capabilities import RateLimit

logger = get_logger("marketdata.ratelimit")


class AdaptiveRateLimiter:
    """Sliding-window budget across several limits, with AIMD adaptation."""

    def __init__(self, limits: Iterable[RateLimit] = (),
                 floor_interval_s: float = 0.0,
                 backoff_factor: float = 2.0,
                 recover_factor: float = 0.5,
                 initial_penalty_s: float = 0.25,
                 max_penalty_s: float = 30.0,
                 rewards_to_recover: int = 10) -> None:
        self.limits: Tuple[RateLimit, ...] = tuple(limits)
        #: minimum spacing between requests regardless of window budget. Set
        #: from the provider's own floor; the AIMD penalty adds to it.
        self.floor_interval_s = float(floor_interval_s)
        self.backoff_factor = float(backoff_factor)
        self.recover_factor = float(recover_factor)
        self.initial_penalty_s = float(initial_penalty_s)
        self.max_penalty_s = float(max_penalty_s)
        #: clean requests required before the penalty is decayed one step. A
        #: penalty that evaporates on the first success oscillates: we would
        #: re-provoke the same rejection immediately.
        self.rewards_to_recover = int(rewards_to_recover)

        #: send times per limit, oldest first
        self._windows: Dict[int, Deque[float]] = {
            i: deque() for i in range(len(self.limits))}
        self._last_sent: Optional[float] = None
        self._penalty_s = 0.0
        self._clean_streak = 0
        #: counters, for MarketState and the operator
        self.sent = 0
        self.rejections = 0
        self.deferrals = 0

    # ------------------------------------------------------------ budgeting

    def _trim(self, now: float) -> None:
        for i, limit in enumerate(self.limits):
            window = self._windows[i]
            cutoff = now - limit.per_seconds
            while window and window[0] <= cutoff:
                window.popleft()

    def next_available(self, now: float) -> float:
        """Monotonic time at which the next request may be sent.

        ``<= now`` means "send it". The caller must then call :meth:`record`;
        this object cannot observe requests it is not told about.
        """
        self._trim(now)
        earliest = now
        # spacing floor + whatever penalty the provider has taught us
        if self._last_sent is not None:
            earliest = max(earliest,
                           self._last_sent + self.floor_interval_s
                           + self._penalty_s)
        # every window must have room; the binding one wins
        for i, limit in enumerate(self.limits):
            window = self._windows[i]
            if len(window) >= limit.capacity:
                # the oldest send in this window has to age out first
                earliest = max(earliest, window[0] + limit.per_seconds)
        return earliest

    def ready(self, now: float) -> bool:
        return self.next_available(now) <= now

    def wait_time(self, now: float) -> float:
        return max(0.0, self.next_available(now) - now)

    def record(self, now: float) -> None:
        """Register a request as sent at ``now``."""
        self._trim(now)
        for window in self._windows.values():
            window.append(now)
        self._last_sent = now
        self.sent += 1

    def defer(self) -> None:
        """Note that a request could not be sent this poll (for reporting)."""
        self.deferrals += 1

    # ----------------------------------------------------------- adaptation

    def penalize(self, now: Optional[float] = None) -> float:
        """The provider rejected us for rate. Widen spacing multiplicatively.

        Returns the new penalty. ``now`` is accepted so a caller can attribute
        the event in a trace; the penalty itself is time-independent.
        """
        self.rejections += 1
        self._clean_streak = 0
        if self._penalty_s <= 0:
            self._penalty_s = self.initial_penalty_s
        else:
            self._penalty_s = min(self.max_penalty_s,
                                  self._penalty_s * self.backoff_factor)
        logger.warning("rate limited - spacing widened to +%.2fs "
                       "(%d rejection(s) so far)", self._penalty_s,
                       self.rejections)
        return self._penalty_s

    def reward(self) -> None:
        """A clean response. Decay the penalty once a streak is established."""
        if self._penalty_s <= 0:
            return
        self._clean_streak += 1
        if self._clean_streak < self.rewards_to_recover:
            return
        self._clean_streak = 0
        self._penalty_s *= self.recover_factor
        if self._penalty_s < 0.01:
            self._penalty_s = 0.0
            logger.info("rate-limit penalty cleared - back to the "
                        "provider floor")

    # ------------------------------------------------------------ reporting

    @property
    def penalty_s(self) -> float:
        return self._penalty_s

    def remaining(self, now: float) -> List[dict]:
        """Headroom per window - what the dashboard shows and what the
        scheduler checks before planning a large pass."""
        self._trim(now)
        out = []
        for i, limit in enumerate(self.limits):
            used = len(self._windows[i])
            out.append({
                "name": limit.name or f"{limit.capacity}/{limit.per_seconds}s",
                "capacity": limit.capacity,
                "used": used,
                "remaining": max(0, limit.capacity - used),
                "window_seconds": limit.per_seconds,
            })
        return out

    def snapshot(self, now: float) -> dict:
        return {
            "sent": self.sent,
            "rejections": self.rejections,
            "deferrals": self.deferrals,
            "penalty_seconds": round(self._penalty_s, 3),
            "floor_interval_seconds": self.floor_interval_s,
            "wait_seconds": round(self.wait_time(now), 3),
            "windows": self.remaining(now),
        }

    def affordable(self, now: float, window_seconds: float = 3600.0) -> int:
        """How many more requests fit in the window of ``window_seconds``.

        Used to answer "can this universe be served at this cadence?" before a
        session starts, rather than discovering the answer at 12:30.
        """
        self._trim(now)
        budgets = []
        for i, limit in enumerate(self.limits):
            if limit.per_seconds > window_seconds:
                # a longer window than asked about: pro-rate its capacity
                budgets.append(int(limit.capacity
                                   * (window_seconds / limit.per_seconds))
                               - len(self._windows[i]))
            else:
                cycles = max(1.0, window_seconds / limit.per_seconds)
                budgets.append(int(limit.capacity * cycles)
                               - len(self._windows[i]))
        return max(0, min(budgets)) if budgets else 1 << 30
