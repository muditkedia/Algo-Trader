"""Market data subsystem (v2) - the ONE owner of market updates.

Everything the trading system knows about the market comes from here, and
nothing else fetches. The scanner, strategies, execution engine, risk engine
and dashboard read :class:`~algo.marketdata.state.MarketState`; they cannot
tell whether a candle arrived by REST, by websocket, from a CSV replay or from
a simulator, because the transport is on the other side of the source seam.

Layering (each arrow is one-way; nothing points back up)::

    TimeframeScheduler   decides WHAT is due          (scheduler.py)
            |
            v
    RequestQueue         orders and de-duplicates     (queue.py)
            |
            v
    Transport            decides HOW to send it       (transport.py)
            |
            v
    MarketDataSource     provider-specific I/O        (sources/)
            |
            v
    MarketState          the single runtime truth     (state.py)

:class:`~algo.marketdata.service.MarketDataService` is the only object that
holds all five. It exposes ``poll()``: one bounded, non-blocking step of that
pipeline, driven by the caller's existing loop. There is deliberately no
thread, no timer and no background task anywhere in this package - a second
loop is a second owner, and two owners of market data is the defect this
subsystem exists to remove.
"""

from algo.marketdata.capabilities import (
    ProviderCapabilities, RateLimit, SMARTAPI_CAPABILITIES,
)
from algo.marketdata.localcandles import LocalCandleEngine
from algo.marketdata.queue import DataRequest, RequestQueue
from algo.marketdata.ratelimit import AdaptiveRateLimiter
from algo.marketdata.scheduler import TimeframeScheduler
from algo.marketdata.service import MarketDataService, PollReport
from algo.marketdata.source import (
    MarketDataSource, NullSource, ProviderSource, Quote, for_provider,
)
from algo.marketdata.state import MarketState, SymbolHealth, TimeframeHealth
from algo.marketdata.streaming import (
    STREAMING_CAPABILITIES, StreamingCandleSource,
)
from algo.marketdata.transport import FetchResult, Transport

__all__ = [
    "AdaptiveRateLimiter", "DataRequest", "FetchResult", "LocalCandleEngine",
    "MarketDataService", "MarketDataSource", "MarketState", "NullSource",
    "PollReport", "ProviderCapabilities", "ProviderSource", "Quote",
    "RateLimit", "RequestQueue", "SMARTAPI_CAPABILITIES",
    "STREAMING_CAPABILITIES", "StreamingCandleSource", "SymbolHealth",
    "TimeframeHealth", "TimeframeScheduler", "Transport", "for_provider",
]
