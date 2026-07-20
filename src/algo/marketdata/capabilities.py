"""ProviderCapabilities - what a source can actually do, declared not assumed.

The scheduler plans around these numbers, so they are DATA rather than
behaviour: a provider that gains a bulk endpoint changes one declaration and
the scheduler adapts, and a provider that has none is planned around instead of
being asked and failing.

Every SmartAPI figure below was VERIFIED, not inferred. Provenance is recorded
per field so the next person does not have to re-derive it:

* ``candle_symbols_per_request = 1`` - read off the installed SDK
  (``SmartApi.smartConnect.getCandleData``, smartapi-python 1.5.5). It takes a
  single ``historicDataParams`` dict carrying ONE ``symboltoken``; there is no
  array form and no bulk historical route in ``_routes``. **Historical candles
  cannot be batched on this provider.** That is a constraint to design around,
  not a gap to work around.
* ``quote_symbols_per_request = 50`` - ``getMarketData(mode, exchangeTokens)``
  exists in the same SDK and takes ``{exchange: [token, ...]}``. Angel One's
  announcement of the endpoint titles it "50-Symbol Bulk Fetch and 1 Request
  Per Second Rate Limit", and its sample response carries an ``unfetched``
  array - tokens past the cap come back unfetched rather than raising.
* Rate limits - Angel One's published table (forum topic 4387): candles 3/s,
  180/min, 5000/hour; quote 10/s, 500/min, 5000/hour. The quote announcement
  post states 1/s for that endpoint, which contradicts the table; we take the
  LOWER of the two. The team acknowledged errors in that table, so the
  conservative reading is the correct one to build on.

THE HOURLY CAP IS THE ONE THAT BINDS AT SCALE, and it is invisible if you only
model requests per second. One candle request per symbol per bar costs
``symbols x bars_per_hour`` per hour: 1000 symbols on 15m is 4,000/hour against
a 5,000 ceiling, and the same universe on 5m is 12,000/hour - impossible, at
any request rate. See ``docs/MARKET_DATA_ARCHITECTURE_V2.md`` for the table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Tuple


@dataclass(frozen=True)
class RateLimit:
    """``capacity`` requests per ``per_seconds`` window, enforced as a sliding
    window rather than a fixed bucket.

    Sliding matters for the hour: a fixed bucket that resets on the hour lets a
    caller spend 5,000 requests at 10:59 and 5,000 more at 11:01, which the
    provider will reject even though a naive counter says both were legal.
    """

    capacity: int
    per_seconds: float
    name: str = ""

    def __post_init__(self) -> None:
        if self.capacity <= 0 or self.per_seconds <= 0:
            raise ValueError("rate limit capacity and window must be positive")

    @property
    def per_second(self) -> float:
        return self.capacity / self.per_seconds


@dataclass(frozen=True)
class ProviderCapabilities:
    """What one source can do, and at what cost."""

    name: str

    #: Symbols servable by ONE historical-candle request. 1 means no batching:
    #: the scheduler must plan a request per symbol and the rate limit is the
    #: universe ceiling. Never set this above what the provider documents -
    #: an optimistic value silently drops symbols.
    candle_symbols_per_request: int = 1

    #: Symbols servable by ONE quote request. 0 means the provider has no bulk
    #: quote endpoint and quotes must be polled per symbol (or not at all).
    quote_symbols_per_request: int = 0

    #: Largest window one candle request may span, per timeframe (days). Fewer,
    #: larger requests beat many small ones for backfill; irrelevant for the
    #: live tail, which is always a bar or two.
    max_days_per_request: Mapping[str, int] = field(default_factory=dict)

    #: Limits on the historical-candle channel, ALL enforced simultaneously.
    candle_limits: Tuple[RateLimit, ...] = ()

    #: Limits on the quote channel (independent budget from candles).
    quote_limits: Tuple[RateLimit, ...] = ()

    #: Requests that may be genuinely in flight at once. 1 = strictly serial.
    #: Left at 1 for SmartAPI: the published limits are per-account, so
    #: concurrency buys throughput only until the shared budget binds, and it
    #: makes rate-limit rejection bursty rather than smooth. Concurrency is a
    #: transport concern; declaring it here keeps the scheduler honest about
    #: what it may plan for.
    max_concurrent_requests: int = 1

    #: Whether the source can PUSH updates instead of being polled. False for
    #: every REST source. The scheduler consults this rather than assuming
    #: polling: a streaming source reports its own freshness and needs no
    #: due-symbol computation, which is the seam a websocket transport slots
    #: into without the scanner noticing (see the v2 doc, "Websocket
    #: readiness").
    supports_streaming: bool = False

    #: Set when the numbers above are guesses rather than verified. Nothing in
    #: the system reads this; it exists so an unverified provider declaration
    #: cannot masquerade as an audited one.
    verified: bool = False

    def candles_batchable(self) -> bool:
        return self.candle_symbols_per_request > 1

    def quotes_batchable(self) -> bool:
        return self.quote_symbols_per_request > 1

    def candle_chunk_days(self, timeframe: str, default: int = 30) -> int:
        return int(self.max_days_per_request.get(timeframe, default))

    def quote_batches(self, symbols) -> list:
        """Split ``symbols`` into requests this provider will actually serve.

        One list per request. A provider with no bulk endpoint yields one
        symbol per request, so callers need no branch for the two cases.
        """
        size = max(1, int(self.quote_symbols_per_request))
        items = list(symbols)
        return [items[i:i + size] for i in range(0, len(items), size)]

    def hourly_candle_budget(self) -> int:
        """Requests per hour the candle channel allows (0 = undeclared).

        The scheduler reports this against planned demand at startup, because
        exceeding it is a configuration error that otherwise only shows up
        mid-session as a wall of rejections.
        """
        budgets = [int(lim.capacity * (3600.0 / lim.per_seconds))
                   for lim in self.candle_limits]
        return min(budgets) if budgets else 0


#: Documented max days per getCandleData request, per interval. Kept here (not
#: in the provider) because it is a capability, and the scheduler sizes backfill
#: windows from it.
SMARTAPI_MAX_DAYS = {
    "1m": 30, "3m": 60, "5m": 100, "10m": 100, "15m": 200, "30m": 200,
    "1h": 400, "1d": 2000,
}

#: Angel One SmartAPI, as verified above. This is the only place those numbers
#: are written down.
SMARTAPI_CAPABILITIES = ProviderCapabilities(
    name="smartapi",
    candle_symbols_per_request=1,          # SDK: one symboltoken per request
    quote_symbols_per_request=50,          # getMarketData bulk fetch
    max_days_per_request=SMARTAPI_MAX_DAYS,
    candle_limits=(
        RateLimit(3, 1.0, "candles/s"),
        RateLimit(180, 60.0, "candles/min"),
        RateLimit(5000, 3600.0, "candles/hour"),
    ),
    quote_limits=(
        RateLimit(1, 1.0, "quote/s"),      # conservative: announcement, not table
        RateLimit(500, 60.0, "quote/min"),
        RateLimit(5000, 3600.0, "quote/hour"),
    ),
    max_concurrent_requests=1,
    supports_streaming=False,
    verified=True,
)

#: A source with no published limits - local stores, replay, simulators. No
#: throttling, no batching claims.
UNLIMITED_CAPABILITIES = ProviderCapabilities(
    name="local",
    candle_symbols_per_request=1,
    quote_symbols_per_request=0,
    max_days_per_request=SMARTAPI_MAX_DAYS,
    candle_limits=(),
    quote_limits=(),
    max_concurrent_requests=1,
    supports_streaming=False,
    verified=True,
)
