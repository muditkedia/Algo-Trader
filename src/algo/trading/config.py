"""TradingConfig - the single configuration object for the production system.

Everything the pipeline needs is declared here; no component reads globals.
``mode`` selects the execution adapter and NOTHING else - every other field
applies identically to paper and live (the one-system philosophy).

Live trading is DISARMED by default and needs three independent keys:
``mode == "live"`` AND ``live_trading_enabled`` AND the environment variable
``ALGO_ENABLE_LIVE=YES``. The AngelOneBroker constructor enforces this
(defence in depth - a mis-set config alone cannot trade).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from algo.core.config import from_dict

LIVE_ENV_KEY = "ALGO_ENABLE_LIVE"
LIVE_ENV_VALUE = "YES"


#: Fraction of the day's capital that may be COMMITTED to one position.
MAX_PER_TRADE_FRACTION = 0.5

#: Default fraction of the day's capital a position must reach to be worth
#: taking. Configurable per session (RiskLimits.min_trade_allocation) - it is
#: an operator policy, not a constant of the system.
MIN_TRADE_ALLOCATION = 0.25


@dataclass(frozen=True)
class RiskLimits:
    """Daily trading configuration.

    THE OPERATOR CONFIGURES TWO NUMBERS::

        "capital": {"deploy_today": 300000, "max_daily_loss": 10000}

    Everything else about capital is DERIVED, so there is no portfolio value,
    per-trade stake, or per-trade risk amount to set or keep in sync:

      * portfolio value      = deploy_today (there is no separate variable)
      * max capital / trade  = deploy_today / 2
      * risk allowed on a new trade = whatever is LEFT of max_daily_loss after
        realized losses, the open risk of existing positions and the risk
        reserved by working entry orders (algo.trading.risk.PortfolioRisk)

    The remaining fields are operational safety limits, not capital inputs;
    they have working defaults and rarely need touching.
    """

    # ------------------------------------------------- operator inputs (2)
    #: capital available to deploy in TODAY's session (account currency)
    deploy_today: float = 300_000.0
    #: trading stops opening new positions once realized+unrealized P&L
    #: reaches -max_daily_loss (existing behaviour, unchanged)
    max_daily_loss: float = 10_000.0

    #: Smallest share of the day's capital a position must reach to be worth
    #: taking, as a fraction of ``deploy_today`` (0.25 = 25%). Below this the
    #: trade is SKIPPED rather than shrunk: a position too small to matter
    #: still pays full round-trip costs and still consumes one of the
    #: ``max_open_positions`` slots, so taking it is worse than not trading.
    #: Set to 0 to disable the floor entirely.
    min_trade_allocation: float = MIN_TRADE_ALLOCATION

    # -------------------------------------------- operational safety limits
    #: hard cap on simultaneously open positions
    max_open_positions: int = 5
    #: one position per symbol; one position per (symbol, strategy) always
    allow_multiple_strategies_per_symbol: bool = False
    #: consecutive broker/data errors that trip the circuit breaker
    circuit_breaker_errors: int = 5
    #: exchange lot size (1 for NSE cash equity; F&O instruments differ)
    lot_size: int = 1

    # ------------------------------------------------------- derived values

    @property
    def portfolio_value(self) -> float:
        """The day's portfolio value IS the capital deployed today."""
        return self.deploy_today

    @property
    def max_per_trade(self) -> float:
        """Most capital any single position may commit."""
        return self.deploy_today * MAX_PER_TRADE_FRACTION

    @property
    def min_per_trade(self) -> float:
        """Least capital a position must commit to be worth taking."""
        return self.deploy_today * max(0.0, float(self.min_trade_allocation))

    @classmethod
    def from_dict(cls, data) -> "RiskLimits":
        return from_dict(cls, data)


@dataclass(frozen=True)
class TradingConfig:
    #: "paper" | "live" - selects the execution adapter, nothing else
    mode: str = "paper"
    #: second key for live (third is the environment variable)
    live_trading_enabled: bool = False

    #: Explicit watchlist file (one symbol per line). When set it wins, which
    #: keeps small fixed lists easy. Leave it empty to let the ``universe``
    #: block below build a liquidity-ranked universe instead.
    symbols_file: str = "nifty100.txt"
    #: Market-universe declaration (algo.trading.universe.UniverseSpec):
    #: {"tier": "paper"} or {"tier": "production", "size": 1000, ...}.
    universe: dict = field(default_factory=dict)
    #: market-data store root (candles) - shared with the rest of the platform
    store_dir: str = "user_data/data/nse"
    #: state, event-log and summary root for the production system
    state_dir: str = "user_data/trading"
    #: evidence database (signals/trades journal)
    evidence_db: str = "user_data/evidence/production.sqlite"

    #: Live timeframes to scan. Leave EMPTY (the default) to use exactly what
    #: the enabled strategies declare - the strategies are the source of truth
    #: for what data the engine needs, so no list has to be kept in sync by
    #: hand. Set it only to RESTRICT the run to a subset of those.
    timeframes: tuple = ()
    #: bars a symbol may lag the expected bar before its price is called STALE.
    #: One bar absorbs the normal bar-close -> grace -> fetch window.
    stale_tolerance_bars: int = 1
    #: seed window (days) for a symbol with no stored bars on the LIVE path.
    #: Paper trading needs live updates, not a year of backfill per symbol.
    live_lookback_days: int = 5
    #: bars of history handed to strategies (full-history semantics for
    #: path-dependent indicators; ~3 months of 15m bars)
    history_bars: int = 1600
    #: seconds after a bar boundary before fetching/evaluating (feed latency)
    bar_grace_seconds: int = 20

    # --------------------------------------------------- market-data pacing
    #: Ceiling on requests issued in ONE ``MarketDataService.poll``, and on the
    #: wall-clock time that poll may take. Together they bound how long the
    #: trading loop can be inside the feed: a 99-symbol pass at ~3 req/s spans
    #: ~33 seconds and is deliberately spread over many polls, so square-off
    #: and the kill switch keep running while it is in flight.
    max_requests_per_poll: int = 64
    poll_budget_seconds: float = 2.0
    #: Scan a due timeframe after this many seconds even if some symbols are
    #: still unserved (0 = always wait for the pass to finish). A late scan on
    #: named partial data beats a skipped one: freshness reports exactly which
    #: symbols are behind, and those are excluded from the scan anyway.
    scan_deadline_seconds: float = 90.0
    #: intraday square-off wall-clock (IST)
    squareoff_hour: int = 15
    squareoff_minute: int = 15
    #: no NEW entries after this wall-clock time (IST)
    entry_cutoff_hour: int = 15
    entry_cutoff_minute: int = 0

    #: presence of this file triggers the emergency stop (square-off + halt)
    kill_switch_file: str = "user_data/trading/KILL"

    #: where the read-only dashboard JSON snapshots are written. Defaults
    #: inside the dashboard folder so ONE static file server serves both the
    #: UI and its data. Purely observational - see algo/trading/dashboard.py.
    dashboard_dir: str = "dashboard/dashboard_data"
    dashboard_enabled: bool = True

    risk: RiskLimits = field(default_factory=RiskLimits)

    #: paper-fill slippage per side (fraction) applied by PaperBroker
    paper_slippage_pct: float = 0.0002

    #: Live last-traded-price marking for OPEN positions (display + P&L only;
    #: no trading decision reads these - stops, targets and entries all use
    #: completed bars). Bounded by max_open_positions, polled at most every
    #: ``live_quote_seconds`` so broker request limits are respected.
    live_quotes_enabled: bool = True
    live_quote_seconds: float = 5.0
    #: how often the fast dashboard tier is rewritten (seconds)
    live_export_seconds: float = 1.0

    #: live broker robustness (AngelOneBroker): min seconds between SDK calls
    #: (order rate-limit compliance), transient-failure retries, backoff base,
    #: and how often the loop refreshes the session JWT (seconds).
    broker_min_interval_s: float = 1.0
    broker_max_retries: int = 3
    broker_retry_backoff_s: float = 1.0
    session_refresh_seconds: int = 1800

    @classmethod
    def from_dict(cls, data) -> "TradingConfig":
        """Build the config. The daily capital settings may be written as a
        ``capital`` block (the documented operator form) and/or a ``risk``
        block for the operational safety limits::

            {"capital": {"deploy_today": 300000, "max_daily_loss": 10000}}

        Both are merged into ``RiskLimits``; ``capital`` wins on conflict
        because it is the operator-facing name.
        """
        data = dict(data or {})
        merged = dict(data.pop("risk", None) or {})
        merged.update(data.pop("capital", None) or {})
        risk = RiskLimits.from_dict(merged)
        cfg = from_dict(cls, data)
        object.__setattr__(cfg, "risk", risk)
        return cfg

    # ------------------------------------------------------------- helpers

    def to_dict(self) -> dict:
        """Round-trippable form: ``TradingConfig.from_dict(cfg.to_dict())``
        reproduces this config. Used by the session wizard to derive a
        modified config WITHOUT touching the file on disk."""
        from dataclasses import asdict
        return asdict(self)

    def effective_timeframes(self, declared) -> tuple:
        """The live timeframes to run, given what the strategies declare.

        Strategies own this: each declares the bars it trades, and the engine
        fetches exactly that union - so a strategy cannot be scanned without
        its data, and no timeframe is downloaded that nothing trades.
        ``timeframes`` in config is an optional RESTRICTION (run only part of
        the library today), never an addition, because asking for a timeframe
        no strategy uses would download data nothing reads.
        """
        declared = tuple(dict.fromkeys(declared))       # de-dup, keep order
        if not self.timeframes:
            return declared
        wanted = tuple(dict.fromkeys(self.timeframes))
        kept = tuple(tf for tf in wanted if tf in declared)
        return kept or declared

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    def live_armed(self) -> bool:
        """All three live keys present (config mode + flag + environment)."""
        return (self.is_live and self.live_trading_enabled
                and os.environ.get(LIVE_ENV_KEY) == LIVE_ENV_VALUE)

    def state_path(self, name: str) -> Path:
        p = Path(self.state_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p / name
