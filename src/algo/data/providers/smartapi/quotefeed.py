"""SmartApiQuoteFeed - the production QUOTE WebSocket: normalize + dispatch.

The feed owns exactly three responsibilities:

* **Packet normalizer** - SDK QUOTE packets (integer paise, epoch-ms exchange
  timestamps, token-keyed) become normalized ticks (symbol, epoch seconds,
  rupees, cumulative day volume). Malformed packets are counted, never raised.
* **Tick dispatcher** - every normalized tick goes to ONE callback (the
  LocalCandleEngine's ``on_tick``); the last price per symbol is retained so
  position marking needs no REST quote call at all.
* **Connection lifecycle** - connect on a daemon thread, resubscribe on
  reopen, and RECORD every outage window ``(down_at, up_at)`` so the candle
  layer knows exactly which buckets need historical repair. A watchdog
  timestamp (``last_packet_at``) lets callers detect a silently dead socket.

The SDK client is injectable (``client_factory``) so the whole lifecycle is
exercised in tests with a fake socket - no network, no credentials.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from algo.core.logging import get_logger

logger = get_logger("data.smartapi.quotefeed")

QUOTE_MODE = 2
NSE_CM_EXCHANGE_TYPE = 1
#: tokens per subscribe message (SmartAPI accepts ~1000/socket; chunked
#: conservatively so one oversized frame cannot reject the whole batch)
SUBSCRIBE_CHUNK = 250


def _default_client_factory(session, config):
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2
    jwt = getattr(session.client, "access_token", None)
    if not jwt or not session.feed_token:
        raise RuntimeError("SmartAPI session has no jwt/feed token")
    return SmartWebSocketV2(jwt, config.api_key, config.client_code,
                            session.feed_token, max_retry_attempt=3)


class SmartApiQuoteFeed:
    """QUOTE-mode websocket wrapped for production use."""

    def __init__(self, session, instruments, config=None,
                 client_factory: Optional[Callable] = None,
                 correlation_id: str = "algo-live") -> None:
        self.session = session
        self.instruments = instruments
        self.config = config if config is not None \
            else getattr(session, "config", None)
        self._client_factory = client_factory or _default_client_factory
        self.correlation_id = correlation_id
        self._sws = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._on_tick: Optional[Callable] = None
        self._symbol_by_token: Dict[str, str] = {}
        self._tokens: List[str] = []
        #: symbol -> (price, exchange_epoch_s): live marks, no REST needed
        self.last_price: Dict[str, tuple] = {}
        self.last_packet_at: Optional[float] = None
        self.started = False
        self.connects = 0
        self.disconnects = 0
        self.packets = 0
        self.bad_packets = 0
        self.unknown_tokens = 0
        #: closed outage windows [(down_epoch_s, up_epoch_s)]
        self.outages: List[Tuple[float, float]] = []
        self._down_at: Optional[float] = None

    # ------------------------------------------------------------ lifecycle

    def set_dispatcher(self, on_tick: Callable) -> None:
        """The ONE consumer of normalized ticks (the candle engine)."""
        self._on_tick = on_tick

    def start(self, symbols: Iterable[str]) -> int:
        """Resolve, subscribe and connect on a daemon thread.

        Returns the number of symbols actually subscribed. Symbols without an
        instrument token are logged and skipped - the market-data layer
        reports them through the normal mapping/unavailable path.
        """
        self._set_universe(symbols)
        if not self._tokens:
            logger.warning("quote feed: no resolvable symbols - not starting")
            return 0
        ws_session = self.session.ensure()
        self._sws = self._client_factory(ws_session, self.config)
        self._sws.on_open = self._handle_open
        self._sws.on_data = self._handle_data
        self._sws.on_error = self._handle_error
        self._sws.on_close = self._handle_close
        self._thread = threading.Thread(target=self._sws.connect,
                                        daemon=True, name="smartapi-quote-ws")
        self._thread.start()
        self.started = True
        return len(self._tokens)

    def stop(self) -> None:
        self.started = False
        sws = self._sws
        if sws is not None:
            try:
                sws.close_connection()
            except Exception as exc:      # shutdown must never raise
                logger.debug("quote feed close: %s", exc)
        if self._thread is not None:
            self._thread.join(timeout=5)

    def resubscribe(self, symbols: Iterable[str]) -> int:
        """Switch the subscription to a new universe without restarting.

        Used by the daily universe refresh. The candle engine is resynced by
        the caller (``mark_gap``) because per-symbol volume baselines from the
        old subscription do not carry over safely.
        """
        old = self._sws
        self._set_universe(symbols)
        if not self.started:
            return len(self._tokens)
        # simplest robust switch: drop the socket; the SDK/our thread rebuilds
        # the subscription from the new token list on reconnect
        try:
            if old is not None:
                old.close_connection()
        except Exception as exc:
            logger.debug("resubscribe close: %s", exc)
        if self._thread is not None:
            self._thread.join(timeout=5)
        return self.start(list(self._symbol_by_token.values()))

    def _set_universe(self, symbols: Iterable[str]) -> None:
        mapping: Dict[str, str] = {}
        skipped = []
        for symbol in symbols:
            token = self.instruments.token_for(symbol)
            if token is None:
                skipped.append(symbol)
                continue
            mapping[str(token)] = symbol
        if skipped:
            logger.warning("quote feed: %d symbol(s) have no token and are "
                           "not subscribed (first: %s)", len(skipped),
                           ", ".join(skipped[:5]))
        with self._lock:
            self._symbol_by_token = mapping
            self._tokens = sorted(mapping)

    # ------------------------------------------------------------ callbacks

    def _handle_open(self, wsapp) -> None:
        self.connects += 1
        now = time.time()
        if self._down_at is not None:
            self.outages.append((self._down_at, now))
            self._down_at = None
        for i in range(0, len(self._tokens), SUBSCRIBE_CHUNK):
            chunk = self._tokens[i:i + SUBSCRIBE_CHUNK]
            self._sws.subscribe(self.correlation_id, QUOTE_MODE,
                                [{"exchangeType": NSE_CM_EXCHANGE_TYPE,
                                  "tokens": chunk}])
        logger.info("quote feed connected (#%d) - %d token(s) subscribed",
                    self.connects, len(self._tokens))

    def _handle_data(self, wsapp, packet) -> None:
        if not isinstance(packet, dict):
            return
        self.packets += 1
        self.last_packet_at = time.time()
        tick = self.normalize(packet)
        if tick is None:
            return
        symbol, ts, price, cum_volume = tick
        self.last_price[symbol] = (price, ts)
        if self._on_tick is not None:
            self._on_tick(symbol, ts, price, cum_volume)

    def _handle_error(self, wsapp, error) -> None:
        logger.warning("quote feed error: %s", error)

    def _handle_close(self, wsapp, *args) -> None:
        self.disconnects += 1
        if self._down_at is None:
            self._down_at = time.time()
        if self.started:
            logger.warning("quote feed closed (#%d) - SDK retry in progress",
                           self.disconnects)

    # ------------------------------------------------------------ normalize

    def normalize(self, packet: dict) -> Optional[tuple]:
        """SDK QUOTE packet -> (symbol, epoch_s, price_rupees, cum_volume).

        Returns None (and counts) when the packet cannot be used: unknown
        token, missing timestamp, or a non-positive price.
        """
        token = str(packet.get("token") or "")
        with self._lock:
            symbol = self._symbol_by_token.get(token)
        if symbol is None:
            self.unknown_tokens += 1
            return None
        try:
            ts_ms = int(packet.get("exchange_timestamp") or 0)
            ltp_paise = packet.get("last_traded_price")
            price = float(ltp_paise) / 100.0 if ltp_paise is not None else 0.0
        except (TypeError, ValueError):
            self.bad_packets += 1
            return None
        if ts_ms <= 0 or price <= 0:
            self.bad_packets += 1
            return None
        volume = packet.get("volume_trade_for_the_day")
        cum_volume = int(volume) if volume is not None else None
        return symbol, ts_ms / 1000.0, price, cum_volume

    # ---------------------------------------------------------- observation

    def seconds_since_packet(self, now: Optional[float] = None
                             ) -> Optional[float]:
        if self.last_packet_at is None:
            return None
        return (now or time.time()) - self.last_packet_at

    def open_outage_since(self) -> Optional[float]:
        return self._down_at

    def snapshot(self) -> dict:
        return {
            "started": self.started,
            "connects": self.connects,
            "disconnects": self.disconnects,
            "packets": self.packets,
            "bad_packets": self.bad_packets,
            "unknown_tokens": self.unknown_tokens,
            "subscribed": len(self._tokens),
            "seconds_since_packet": self.seconds_since_packet(),
            "outages": len(self.outages),
        }
