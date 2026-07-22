"""DataRequest / RequestQueue - the buffer between deciding and sending.

The scheduler produces requests; the transport consumes them. Neither knows the
other exists, and this queue is the only thing they share. That separation is
what lets a websocket transport replace REST later: the scheduler keeps
emitting "RELIANCE needs its 09:30 15m bar" and something else decides that the
answer is already in a stream.

Two properties the queue owns, because neither end can:

* **De-duplication.** The same symbol/timeframe cannot be queued twice. Without
  this, a poll that runs before the previous request was served re-enqueues the
  whole due set every time, and the queue grows without bound while the
  provider sees the same request repeatedly.
* **Ordering by urgency.** A queue served in arrival order lets a 1h backfill
  sit in front of the 15m bar that a scan is waiting on. Priority is explicit
  and total, so ordering is deterministic and testable.
"""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

#: Request kinds. Candles feed decisions; quotes only ever feed display and
#: position marking, which is why they are a separate channel with a separate
#: budget - a burst of quote polling must never delay a decision-bearing bar.
CANDLES = "candles"
QUOTES = "quotes"

#: Priority bands (lower is served first). Bands rather than a free-form int so
#: that "held position" always outranks "routine scan" no matter how the
#: within-band tiebreak is computed.
P_HELD = 0          # a symbol we carry risk in
P_DUE = 10          # a timeframe whose bar has closed and is being waited on
P_BACKFILL = 20     # filling a gap; correctness, not urgency
P_QUOTE = 30        # display only


@dataclass(order=False)
class DataRequest:
    """One unit of work for the transport. Immutable once queued."""

    kind: str
    timeframe: str
    symbols: Tuple[str, ...]
    start: Optional[pd.Timestamp] = None
    end: Optional[pd.Timestamp] = None
    priority: int = P_DUE
    #: the completed bar this request is meant to deliver (None for quotes and
    #: backfills). Carried so the scheduler can tell "served" from "returned
    #: nothing useful" without re-deriving the expectation.
    due_bar: Optional[pd.Timestamp] = None
    #: why this exists, for the event log and the failure path
    reason: str = ""
    attempts: int = 0

    @property
    def key(self) -> Tuple:
        """Identity for de-duplication."""
        return (self.kind, self.timeframe, self.symbols)

    @property
    def symbol(self) -> str:
        """The single symbol, for the un-batched candle path."""
        return self.symbols[0]

    def describe(self) -> str:
        head = self.symbols[0] if self.symbols else "-"
        more = f" +{len(self.symbols) - 1}" if len(self.symbols) > 1 else ""
        window = ""
        if self.start is not None:
            window = f" [{self.start} -> {self.end}]"
        return f"{self.kind}/{self.timeframe} {head}{more}{window}"


class RequestQueue:
    """Priority queue with de-duplication and stable, deterministic ordering."""

    def __init__(self, max_depth: int = 100_000) -> None:
        self._heap: List[tuple] = []
        self._queued: Dict[Tuple, DataRequest] = {}
        self._counter = itertools.count()
        #: a bound, so a provider that never serves anything cannot turn a
        #: queue into a memory leak over a session
        self.max_depth = int(max_depth)
        self.dropped = 0

    def __len__(self) -> int:
        return len(self._queued)

    def __bool__(self) -> bool:
        return bool(self._queued)

    def push(self, request: DataRequest) -> bool:
        """Queue a request. Returns False if it was a duplicate or the queue is
        full - never raises, because a full queue is a load condition, not a
        programming error."""
        existing = self._queued.get(request.key)
        if existing is not None:
            if not self._supersedes(existing, request):
                return False
            self._queued[request.key] = request
            heapq.heappush(self._heap,
                           (request.priority, next(self._counter), request))
            return True
        if len(self._queued) >= self.max_depth:
            self.dropped += 1
            return False
        # (priority, insertion order) is a total order: equal priorities keep
        # arrival order, so the same inputs always produce the same sequence
        heapq.heappush(self._heap,
                       (request.priority, next(self._counter), request))
        self._queued[request.key] = request
        return True

    def extend(self, requests: Iterable[DataRequest]) -> int:
        return sum(1 for r in requests if self.push(r))

    def pop(self) -> Optional[DataRequest]:
        while self._heap:
            _, _, request = heapq.heappop(self._heap)
            if self._queued.get(request.key) is request:
                self._queued.pop(request.key, None)
                return request
        return None

    def peek(self) -> Optional[DataRequest]:
        while self._heap:
            request = self._heap[0][2]
            if self._queued.get(request.key) is request:
                return request
            heapq.heappop(self._heap)
        return None

    def clear(self) -> None:
        self._heap.clear()
        self._queued.clear()

    def contains(self, request: DataRequest) -> bool:
        return request.key in self._queued

    def depth_by_kind(self) -> dict:
        out: dict = {}
        for req in self._queued.values():
            out[req.kind] = out.get(req.kind, 0) + 1
        return out

    @staticmethod
    def _supersedes(existing: DataRequest, candidate: DataRequest) -> bool:
        """True when a same-key request should replace the queued one.

        A newer bar can become due while an older request is still queued under
        provider pressure. The newer request covers the old missing range plus
        the new bar; keeping the stale queued request would spend a provider
        call on a window that cannot complete the current pass.
        """
        if existing.kind != CANDLES or candidate.kind != CANDLES:
            return False
        old_due = existing.due_bar
        new_due = candidate.due_bar
        if old_due is not None and new_due is not None \
                and pd.Timestamp(new_due) > pd.Timestamp(old_due):
            return True
        old_end = existing.end
        new_end = candidate.end
        if old_end is not None and new_end is not None \
                and pd.Timestamp(new_end) > pd.Timestamp(old_end):
            return True
        return candidate.priority < existing.priority

    def snapshot(self) -> dict:
        """What the dashboard shows about pending work."""
        head = self.peek()
        return {
            "depth": len(self._queued),
            "by_kind": self.depth_by_kind(),
            "dropped": self.dropped,
            "next": head.describe() if head is not None else None,
        }
