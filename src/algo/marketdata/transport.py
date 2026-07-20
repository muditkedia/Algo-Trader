"""Transport - executes one request. Decides HOW, never WHAT.

Given a :class:`DataRequest` it performs exactly that request against the
source, times it, applies the quality gate, and returns a
:class:`FetchResult`. It contains no notion of which symbols matter, which
timeframe is due, or what should happen next - ask it for something and it
does that thing once.

It owns two things the scheduler must not:

* **Pacing.** One :class:`AdaptiveRateLimiter` per channel (candles and quotes
  have independent provider budgets). The limiter is exposed rather than
  hidden, because dispatch order is a scheduling decision: the service asks
  "may I send?" and chooses what to send, while the transport reports what the
  provider said back into the limiter.
* **The quality gate.** Isolated bad bars are dropped, counted and logged;
  a systematically broken response is quarantined whole. The tolerance is
  ``max(max_invalid_rows, pct x rows)`` - the absolute floor matters because
  one bad bar in an 877-row daily history is 0.11% while the identical defect
  in 15m data is 0.005% (D-020). This is the ingestion engine's rule,
  unchanged, because changing it would silently change what data exists.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional

import pandas as pd

from algo.core.logging import get_logger
from algo.data import ohlcv
from algo.data.quality import QualityReport, check_ohlcv, invalid_row_mask
from algo.marketdata.queue import CANDLES, DataRequest, QUOTES
from algo.marketdata.ratelimit import AdaptiveRateLimiter
from algo.marketdata.source import MarketDataSource, Quote

logger = get_logger("marketdata.transport")


@dataclass
class FetchResult:
    """What came back from one request, and what it cost."""

    request: DataRequest
    ok: bool = False
    frame: Optional[pd.DataFrame] = None
    quotes: Dict[str, Quote] = field(default_factory=dict)
    rows: int = 0
    dropped: int = 0
    latency_s: float = 0.0
    error: str = ""
    #: the provider refused us for rate - not a data problem, and the request
    #: must be retried rather than counted as a symbol failure
    rate_limited: bool = False
    #: the request could not be addressed at all (no token, bad timeframe)
    unavailable: bool = False
    #: served, but the window held no bars. NORMAL when the market is quiet or
    #: the store is current; must never be conflated with the two above.
    empty: bool = False
    quality: Optional[QualityReport] = None

    @property
    def retryable(self) -> bool:
        return self.rate_limited


class Transport:
    """Executes requests against one source, paced per channel."""

    def __init__(self, source: MarketDataSource,
                 candle_limiter: Optional[AdaptiveRateLimiter] = None,
                 quote_limiter: Optional[AdaptiveRateLimiter] = None,
                 calendar=None,
                 max_invalid_row_pct: float = 0.001,
                 max_invalid_rows: int = 5) -> None:
        self.source = source
        caps = source.capabilities
        self.candle_limiter = candle_limiter or AdaptiveRateLimiter(
            caps.candle_limits)
        self.quote_limiter = quote_limiter or AdaptiveRateLimiter(
            caps.quote_limits)
        self.calendar = calendar
        self.max_invalid_row_pct = max_invalid_row_pct
        self.max_invalid_rows = max_invalid_rows

    def limiter_for(self, kind: str) -> AdaptiveRateLimiter:
        return self.quote_limiter if kind == QUOTES else self.candle_limiter

    # ------------------------------------------------------------- dispatch

    def execute(self, request: DataRequest,
                now: Optional[float] = None) -> FetchResult:
        """Perform ``request`` once. Never raises."""
        now = time.monotonic() if now is None else now
        limiter = self.limiter_for(request.kind)
        limiter.record(now)
        started = time.monotonic()
        try:
            if request.kind == QUOTES:
                return self._quotes(request, started, limiter)
            return self._candles(request, started, limiter)
        except Exception as exc:                  # a source must not abort a poll
            latency = time.monotonic() - started
            if self.source.is_rate_limited(exc):
                limiter.penalize(now)
                logger.warning("rate limited on %s", request.describe())
                return FetchResult(request=request, ok=False, latency_s=latency,
                                   error=str(exc), rate_limited=True)
            logger.warning("fetch failed %s: %s", request.describe(), exc)
            return FetchResult(request=request, ok=False, latency_s=latency,
                               error=f"fetch error: {exc}")

    # -------------------------------------------------------------- candles

    def _candles(self, request: DataRequest, started: float,
                 limiter: AdaptiveRateLimiter) -> FetchResult:
        symbol = request.symbol
        reason = self.source.unavailable_reason(symbol, request.timeframe)
        if reason:
            return FetchResult(request=request, ok=False, unavailable=True,
                               error=reason,
                               latency_s=time.monotonic() - started)
        frame = self.source.fetch_candles(symbol, request.timeframe,
                                          request.start, request.end)
        latency = time.monotonic() - started
        limiter.reward()
        if ohlcv.is_empty(frame):
            return FetchResult(request=request, ok=True, empty=True,
                               latency_s=latency, frame=frame,
                               error="no candles in the requested window")
        frame, dropped, report = self._gate(frame, symbol, request.timeframe)
        if frame is None:
            return FetchResult(request=request, ok=False, latency_s=latency,
                               error=report, quality=None)
        return FetchResult(request=request, ok=True, frame=frame,
                           rows=len(frame), dropped=dropped,
                           latency_s=latency, quality=report)

    def _gate(self, frame: pd.DataFrame, symbol: str, timeframe: str):
        """Quality gate. Returns ``(frame|None, dropped, report|reason)``."""
        report = check_ohlcv(frame, symbol, timeframe, self.calendar)
        dropped = 0
        if report.ok:
            return frame, 0, report
        reasons = "; ".join(f"{i.code}:{i.detail}" for i in report.errors)
        mask = invalid_row_mask(frame)
        rate = float(mask.mean()) if len(mask) else 1.0
        n_bad = int(mask.sum())
        allowance = max(self.max_invalid_rows,
                        int(self.max_invalid_row_pct * len(frame)))
        if 0 < n_bad <= allowance:
            dropped = n_bad
            logger.warning("%s/%s: dropping %d invalid bar(s) (%.4f%% of %d) "
                           "then admitting the rest - %s", symbol, timeframe,
                           dropped, rate * 100, len(frame), reasons)
            frame = frame[~mask].reset_index(drop=True)
            report = check_ohlcv(frame, symbol, timeframe, self.calendar)
        if not report.ok:
            reasons = "; ".join(f"{i.code}:{i.detail}" for i in report.errors)
            logger.warning("QUARANTINE %s/%s (%.3f%% bad rows): %s", symbol,
                           timeframe, rate * 100, reasons)
            return None, dropped, reasons
        return frame, dropped, report

    # --------------------------------------------------------------- quotes

    def _quotes(self, request: DataRequest, started: float,
                limiter: AdaptiveRateLimiter) -> FetchResult:
        quotes = self.source.fetch_quotes(request.symbols)
        latency = time.monotonic() - started
        limiter.reward()
        good = {s: q for s, q in (quotes or {}).items() if q.valid()}
        return FetchResult(request=request, ok=True, quotes=good,
                           rows=len(good), latency_s=latency,
                           empty=not good)

    # ------------------------------------------------------------ reporting

    def snapshot(self, now: Optional[float] = None) -> dict:
        now = time.monotonic() if now is None else now
        return {
            "source": self.source.name,
            "status": self.source.status(),
            "candles": self.candle_limiter.snapshot(now),
            "quotes": self.quote_limiter.snapshot(now),
        }
