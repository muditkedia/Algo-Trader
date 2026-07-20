"""ProductionEngine - the trading loop that wires every stage together.

ONE engine for paper and live: it builds the pipeline from a TradingConfig,
runs preflight + recovery before trading, and executes cycles
(poll -> manage open positions -> risk -> scan -> size -> order -> log). The
only mode-dependent object is the execution adapter, chosen by config.

A cycle is idempotent and side-effect-persisted at every step, so a crash
mid-cycle is recovered on the next start. The loop itself is deliberately thin;
all behaviour lives in the single-responsibility stages it calls.

**The engine does not fetch.** It reads
:class:`~algo.marketdata.state.MarketState` and nothing else, and it drives
:class:`~algo.marketdata.service.MarketDataService` by calling ``poll()`` once
per tick. It cannot tell REST from websocket from replay, and there is no other
path by which market data enters the system.

That is a change of ownership, not just of module. The previous design had
THREE independent pollers - the cycle's own ``feed.refresh``, the live tier's
private quote timer, and a bar scheduler that decided cadence separately from
the fetching - so the same symbol could be requested twice in a second while a
due timeframe went unserved. :meth:`tick` is now the single beat: it polls
(bounded), always manages open risk, and scans exactly those timeframes whose
data actually arrived.
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from algo.core.logging import get_logger
from algo.marketdata import MarketDataService
from algo.risk.engine import RiskParams
from algo.trading.adapters import build_adapter
from algo.trading.adapters.paper import PaperBroker
from algo.trading.clock import IST, MarketClock
from algo.trading.config import TradingConfig
from algo.trading.eventlog import EventLog
from algo.trading.models import Position, now_iso
from algo.trading.monitoring import (
    HealthMonitor, daily_summary, render_dashboard, write_summary,
)
from algo.trading.orchestrator import Orchestrator
from algo.trading.ordermanager import OrderManager
from algo.trading.portfolio import PortfolioEngine
from algo.trading.preflight import run_preflight
from algo.trading.recovery import RecoveryManager
from algo.trading.risk import AccountRiskEngine
from algo.trading.trademanager import TradeManager

logger = get_logger("trading.engine")


def load_intraday_strategies() -> list:
    """Every enabled intraday strategy, discovered from the registry (the same
    discovery the backtests use) - no hand-maintained list."""
    from algo.core.enums import HoldingScope
    from algo.strategies.library import ALL_STRATEGIES
    out = []
    for cls in ALL_STRATEGIES:
        if (cls.meta.enabled
                and cls.meta.holding_scope == HoldingScope.INTRADAY):
            out.append(cls())
    return out


class ProductionEngine:
    def __init__(self, config: TradingConfig, adapter=None, state=None,
                 strategies=None, session=None, instruments=None,
                 source=None, marketdata=None) -> None:
        self.config = config
        self.params = RiskParams()
        self.clock = MarketClock.build(
            holiday_file=str(config.state_path("holidays.txt")),
            squareoff=_time(config.squareoff_hour, config.squareoff_minute),
            entry_cutoff=_time(config.entry_cutoff_hour,
                               config.entry_cutoff_minute))
        self.events = EventLog(config.state_dir)
        self.portfolio = PortfolioEngine(config.state_path("portfolio.json"))
        self.strategies = strategies or load_intraday_strategies()
        self.specs = {s.name: s.execution for s in self.strategies}
        #: THE live timeframe set, derived from what the enabled strategies
        #: declare (config may restrict it, never extend it). The market-data
        #: scheduler is built from this same list, so a timeframe nothing
        #: trades is never fetched and a strategy can never be scanned without
        #: its data. One list, read by every stage.
        self.timeframes = list(config.effective_timeframes(
            sorted({s.meta.timeframe for s in self.strategies}))) or ["15m"]
        self.marketdata = marketdata or self._build_marketdata(state, source)
        #: THE single source of market truth. Every stage reads this; nothing
        #: else fetches, and nothing recomputes what it already knows.
        self.state = self.marketdata.state
        self.adapter = adapter or build_adapter(
            config, clock=self.clock, session=session, instruments=instruments)
        self.risk = AccountRiskEngine(config.risk, config.kill_switch_file)
        self.orders = OrderManager(self.adapter, self.portfolio, self.events,
                                   risk=self.risk)
        self.orchestrator = Orchestrator(self.strategies, self.state,
                                         params=self.params)
        self.manager = TradeManager(self.specs, params=self.params)
        self.health = HealthMonitor(self.clock, self.risk)
        self._seq = 0
        #: last reported market-data situation per timeframe, so a persistent
        #: fault is stated when it changes rather than every cycle
        self._data_state: dict = {}
        #: latest FreshnessReport per timeframe - THE data-age source read by
        #: the health beat and the dashboard alike
        self.freshness: dict = {}
        self._fresh_state: dict = {}
        self._last_keepalive = pd.Timestamp.now(tz="UTC")
        # read-only observability: writes JSON snapshots for the dashboard.
        # Never consulted by any trading decision; failures are swallowed.
        self.exporter = None
        if getattr(config, "dashboard_enabled", True):
            from algo.trading.dashboard import DashboardExporter
            self.exporter = DashboardExporter(self)

    def _build_marketdata(self, state, source) -> MarketDataService:
        """Assemble the market-data subsystem for this session.

        ``state`` accepts a pre-built MarketState (tests and replay supply one
        with candles already in place); its store and watchlist then win, since
        a caller that hands us a populated state means it to be used.
        """
        from algo.data.store import MarketDataStore
        from algo.trading.watchlist import build_watchlist

        config = self.config
        if state is not None:
            store, symbols = state.store, list(state.symbols)
            report = getattr(state, "universe_report", None)
        else:
            store = MarketDataStore(config.store_dir)
            watch = build_watchlist(config, store, self.timeframes)
            symbols, report = watch.symbols, watch.report
        service = MarketDataService.build(
            store, source, self.clock, symbols, self.timeframes,
            history_bars=config.history_bars,
            stale_tolerance_bars=config.stale_tolerance_bars,
            live_lookback_days=config.live_lookback_days,
            grace_seconds=config.bar_grace_seconds,
            quote_interval_s=config.live_quote_seconds,
            quotes_enabled=config.live_quotes_enabled,
            scan_deadline_seconds=config.scan_deadline_seconds,
            max_requests_per_poll=config.max_requests_per_poll,
            poll_budget_seconds=config.poll_budget_seconds)
        service.state.universe_report = report
        if state is not None:
            # keep the caller's object identity: tests hold a reference to it
            state.timeframes = list(self.timeframes)
            state.universe_report = report
            if getattr(state, "mapping", None) is None:
                state.mapping = service.source.mapping_report()
            service.state = state
            service.scheduler.bind_source(service.source)
        return service

    @property
    def feed(self):
        """Deprecated alias for :attr:`state`.

        Retained because MarketState IS what the old feed's read surface always
        was - symbols, history, marks, freshness - and renaming every call site
        at once would have been churn on top of a subsystem rewrite. There is
        no second object and no second fetch path behind this name.
        """
        return self.state

    # -------------------------------------------------------- observability

    def _observe(self, method: str, *args, **kwargs) -> None:
        """Call the dashboard exporter, and NEVER let it affect trading.

        The exporter guards its own internals, but the invariant belongs to the
        caller: observation must not be able to stop a cycle. An audit probe
        that replaced ``export`` wholesale did stop one, which means the
        guarantee rested on the exporter being well-behaved rather than on the
        engine refusing to depend on it.
        """
        if self.exporter is None:
            return
        try:
            getattr(self.exporter, method)(*args, **kwargs)
        except Exception as exc:  # pragma: no cover - defensive by design
            logger.warning("dashboard %s failed (trading unaffected): %s",
                           method, exc)

    # ------------------------------------------------------------- startup

    def startup(self) -> bool:
        """Recover, then preflight. Returns True if trading may begin."""
        self.adapter.connect()
        recovery = RecoveryManager(self.portfolio, self.adapter, self.events,
                                   feed=self.state)
        report = recovery.recover()
        # session bookkeeping: a new day resets intraday realized P&L
        today = self.clock.now().date().isoformat()
        if self.portfolio.session_date != today:
            self.portfolio.reset_for_new_session(today)
        pre = run_preflight(self.config, self.adapter, self.state,
                            self.strategies, self.portfolio, self.clock,
                            recovery_report=report)
        self.events.emit("health", phase="startup", preflight=pre.to_dict())
        print(self.risk_summary())
        self._observe("note_scanner", "Engine started", "Preflight complete")
        self._observe("export")
        if not pre.passed:
            logger.error("startup blocked - preflight failed")
        return pre.passed

    # ------------------------------------------------------- scheduled tick

    def tick(self) -> dict:
        """THE beat. One poll of the market-data subsystem, then act on what
        actually arrived.

        Called at loop cadence (about once a second), not once per bar. Every
        tick manages open risk and re-marks positions, so stops and square-off
        never wait on a bar boundary or on a fetch pass finishing. A full scan
        runs only for timeframes the service reports COMPLETE - data that has
        arrived, rather than a timer's belief that it should have.

        This is the only place market data is requested. There is no second
        loop, no background thread and no independent refresh timer anywhere
        in the system.
        """
        self._keepalive_if_due()
        held = [p.symbol for p in self.portfolio.open_positions()]
        report = self.marketdata.poll(held=held)
        results = []
        for tf in report.completed:
            results.append(self.run_cycle(tf))
        if not results:
            # no bar completed: still manage and protect existing risk
            results.append(self.run_cycle(self.timeframes[0], scan=False,
                                          export=False))
        # Mark to the live quote LAST. A cycle marks from completed bars (which
        # is what every decision reads); the quote is newer and display-only,
        # so it must land after, or the panel shows a bar-old price while a
        # current one sits unused in MarketState.
        self._mark_positions()
        self._observe("export_live")
        opened = sum(r["opened"] for r in results)
        return {"due": list(report.completed), "opened": opened,
                "poll": report.to_dict(),
                "stats": self.portfolio.daily_stats()}

    def _check_feed(self) -> bool:
        """Is the market-data subsystem serving? Feeds the circuit breaker.

        ``poll`` never raises - a provider fault must not abort the loop - so
        the fault has to be READ rather than caught. A wired provider that has
        gone OFFLINE counts an error exactly as the old ``feed.refresh``
        exception did, so repeated market-data failure still trips the breaker
        after ``circuit_breaker_errors`` cycles.

        An OFFLINE source that was never wired (paper with no credentials) is
        NOT an error: it is the configured mode, stated at startup, and
        counting it would halt every offline paper session on cycle five.
        """
        status = self.state.provider_status
        configured_offline = self.marketdata.source.status() == "OFFLINE"
        if status == "OFFLINE" and not configured_offline:
            self.risk.record_error()
            self.events.emit("error", where="market_data",
                             detail=self.state.provider_detail)
            return False
        return status != "DEGRADED"

    def _mark_positions(self) -> None:
        """Re-price OPEN positions from the latest quotes the service holds.

        Bounded by ``max_open_positions``, never by universe size: a
        1000-symbol watchlist costs exactly what a 10-symbol one does here.
        Display and P&L only - stops, targets and entries all read completed
        bars, exactly as they were measured.
        """
        held = {p.symbol for p in self.portfolio.open_positions()}
        if not held:
            return
        marks = {s: p for s, p in self.state.quotes.items() if s in held}
        if marks:
            self.portfolio.mark(marks)

    @property
    def quote_prices(self) -> dict:
        """Live marks for open positions. Derived from MarketState, never
        stored a second time - two copies of a price is how one panel showed a
        stale mark while another showed a fresh one."""
        held = {p.symbol for p in self.portfolio.open_positions()}
        return {s: p for s, p in self.state.quotes.items() if s in held}

    @property
    def quote_ts(self):
        return self.state.quotes_ts

    @property
    def scheduler(self):
        """THE market-data scheduler. The engine has no separate bar timer:
        one object decides when a timeframe is due, and it is the same one that
        decides when to fetch it."""
        return self.marketdata.scheduler

    def _keepalive_if_due(self) -> None:
        now = pd.Timestamp.now(tz="UTC")
        if (now - self._last_keepalive).total_seconds() \
                >= self.config.session_refresh_seconds:
            self.adapter.keepalive()
            self._last_keepalive = now

    # ------------------------------------------------------------- one cycle

    def run_cycle(self, timeframe: Optional[str] = None,
                  scan: bool = True, export: bool = True) -> dict:
        """One management+scan pass over data that has ALREADY arrived.

        Safe to call repeatedly; each stage persists its own state. It does not
        fetch: :meth:`tick` polls the market-data subsystem and calls this for
        timeframes whose data landed, so a cycle can never be the thing that
        blocks on a provider.
        """
        timeframe = timeframe or self.timeframes[0]
        # 1) report what the market-data subsystem did, and how old the data
        #    we are about to act on is - measured against the bar the exchange
        #    should have produced by now, per symbol, BEFORE any decision reads
        #    a price (D-039).
        feed_ok = self._check_feed()
        self._report_data_state(timeframe)
        fresh = self._assess_freshness(timeframe)

        # 2) mark open positions to latest completed-bar prices
        prices = self.state.latest_prices(timeframe)
        self.portfolio.mark(prices)
        if isinstance(self.adapter, PaperBroker):
            self.adapter.update_quotes(prices)

        # 3) manage open positions (exits/partials/trails) - ALWAYS, even if
        #    the day is halted (we still protect/close existing risk)
        self._manage_open(timeframe)

        # 4) day-level risk gate; emergency stop squares everything off
        day = self.risk.check_day(self.portfolio)
        past_cut = self.clock.past_entry_cutoff()
        past_sq = self.clock.past_squareoff()
        if past_sq or not day:
            self._squareoff_all("emergency_stop" if not day
                                else "session_squareoff", timeframe)

        # 5) scan for entries (only if the day allows and we're pre-cutoff)
        opened = 0
        if scan and day and not past_cut and not past_sq \
                and not self.risk.emergency_stop_requested():
            opened = self._scan_and_enter(timeframe)

        # data_fresh means the FETCH served the watchlist, not merely that the
        # store holds candles - a store that stopped updating still returns
        # prices, and reporting that as fresh is how a whole session traded on
        # stale bars while every health beat said "ok" (D-038).
        health = self.health.beat(
            adapter_ok=feed_ok,
            data_fresh=bool(prices) and self.state.data_ok(timeframe)
            and (fresh.ok if fresh is not None else True))
        self.events.emit("health", **health, opened=opened,
                         open_positions=self.portfolio.open_count())
        if export:
            self._observe("export")
        return {"opened": opened, "health": health,
                "stats": self.portfolio.daily_stats()}

    # -------------------------------------------------------- diagnostics

    def _assess_freshness(self, timeframe: str):
        """Per-symbol data age for this timeframe, kept for the health beat
        and the dashboard. Announced once when the verdict CHANGES.

        Freshness is a property of the CANDLES, not of the exporter: the first
        paper session displayed prices from a 71-hour-old bar while reporting
        the data as 0 seconds old, because the only age anywhere in the system
        was the snapshot's write time (D-039).
        """
        try:
            report = self.state.freshness(timeframe, clock=self.clock)
        except Exception as exc:      # diagnostics must never break a cycle
            logger.warning("freshness check failed for %s: %s", timeframe, exc)
            return None
        self.freshness[timeframe] = report
        signature = (report.status, len(report.stale), len(report.missing))
        if self._fresh_state.get(timeframe) != signature:
            self._fresh_state[timeframe] = signature
            if report.status == "STALE":
                logger.error("%s", report.headline())
                self.events.emit("data", timeframe=timeframe, ok=False,
                                 kind="stale", stale=len(report.stale),
                                 total=report.total,
                                 bars_behind=report.worst_bars_behind,
                                 examples=[s.symbol for s in report.stale[:5]],
                                 detail=report.headline())
            else:
                logger.info("%s", report.headline())
        return report

    def _report_data_state(self, timeframe: str) -> None:
        """State the market-data outcome ONCE per cycle.

        The operator sees one line - "no new candles" or "unable to fetch N/M,
        examples ..." - instead of one warning per symbol, and the same verdict
        is emitted as an event so the dashboard and the log agree. Repeats are
        suppressed: the message is only re-emitted when the situation CHANGES,
        so a persistent fault does not scroll the console for a whole session.
        """
        state = self.state.diagnosis(timeframe)
        headline = state.get("headline", "")
        blocked = int(state.get("blocked", 0))
        signature = (timeframe, state.get("ok", True), blocked,
                     tuple(sorted(state.get("reasons", {}))))
        changed = self._data_state.get(timeframe) != signature
        self._data_state[timeframe] = signature
        if state.get("ok", True):
            if changed and headline:
                logger.info("%s", headline)
            return
        if not changed:
            # the fault persists and has already been stated. Re-announcing it
            # every cycle is the very noise this replaces - the dashboard's
            # MARKET DATA panel carries the standing condition.
            return
        logger.error("%s", headline)
        self.events.emit(
            "data", timeframe=timeframe, ok=False, blocked=blocked,
            total=state.get("total", 0), reasons=state.get("reasons", {}),
            examples=state.get("examples", []), detail=headline)
        self._observe("note_scanner", f"Market data: {headline}")

    def mapping_report(self):
        """The watchlist resolved through the one authoritative instrument map."""
        try:
            return self.state.mapping_report()
        except Exception as exc:      # diagnostics must never break a cycle
            logger.warning("instrument mapping report unavailable: %s", exc)
            return None

    # ------------------------------------------------------ management

    def _manage_open(self, timeframe: str) -> None:
        for pos in list(self.portfolio.open_positions()):
            if pos.strategy == "__orphan__":
                self._squareoff_position(pos, timeframe, "orphan_squareoff")
                continue
            spec = self.specs.get(pos.strategy)
            if spec is None:
                continue
            frame = self.state.history(pos.symbol, pos.timeframe)
            if frame.empty:
                continue
            bar = frame.iloc[-1]
            decision = self.manager.manage(
                pos, bar, spec=spec, past_squareoff=self.clock.past_squareoff())
            if decision.action == "hold":
                continue
            if decision.action == "trail":
                pos.stop = decision.new_stop
                pos.trailed = True
                self.portfolio.persist()
                self.events.emit("position", action="trail", symbol=pos.symbol,
                                 stop=pos.stop)
                continue
            if decision.action == "partial":
                self._execute_partial(pos, decision)
                continue
            if decision.action == "exit":
                self._execute_exit(pos, decision.price, decision.reason)

    def _execute_partial(self, pos: Position, decision) -> None:
        if isinstance(self.adapter, PaperBroker):
            self.adapter.update_quotes({pos.symbol: decision.price})
        self._seq += 1
        order = self.orders.market_exit(pos, decision.partial_qty, "partial",
                                        seq=self._seq)
        fill = order.avg_fill_price or decision.price
        self.portfolio.book_partial(pos, decision.partial_qty, fill)
        pos.stop = decision.new_stop      # breakeven on remainder
        pos.target = pos.target2
        self.portfolio.persist()
        self.events.emit("position", action="partial", symbol=pos.symbol,
                         price=fill, qty=decision.partial_qty)

    def _execute_exit(self, pos: Position, price: float, reason: str) -> None:
        if isinstance(self.adapter, PaperBroker):
            self.adapter.update_quotes({pos.symbol: price})
        self._seq += 1
        order = self.orders.market_exit(pos, pos.open_quantity, reason,
                                        seq=self._seq)
        fill = order.avg_fill_price or price
        record = self.portfolio.close_position(pos, fill, reason)
        self.events.emit("position", action="close", symbol=pos.symbol,
                         reason=reason, price=fill, pnl=record["pnl"])

    # ---------------------------------------------------------- entries

    def _scan_and_enter(self, timeframe: str) -> int:
        names = [s.name for s in self.strategies
                 if s.meta.timeframe == timeframe]
        # 99 configured, 3 unreachable, 96 scanned: a handful of bad symbols is
        # a data-quality condition handled per symbol, never a reason to stop
        # scanning the ones that are fine.
        usable = len(self.state.usable_symbols(timeframe))
        skipped = len(self.state.skipped_symbols())
        self._observe("note_scanner",
                      f"Scanning {usable} symbols on {timeframe}"
                      + (f" ({skipped} skipped - stale/unavailable)"
                         if skipped else ""),
                      detail=f"Evaluating: {', '.join(names)}")
        signals = self.orchestrator.evaluate(timeframe)
        self._observe("note_scanner",
                      f"{timeframe} scan complete - {len(signals)} signal(s)",
                      detail="Waiting for next completed candle.")
        opened = 0
        for sig in signals:
            self.events.emit("signal", symbol=sig.symbol, strategy=sig.strategy,
                             entry=sig.entry_ref, stop=sig.stop,
                             target=sig.target, confidence=sig.confidence)
            decision = self.risk.check_entry(sig, self.portfolio)
            if not decision:
                self.events.emit("risk_block", symbol=sig.symbol,
                                 strategy=sig.strategy, reason=decision.reason)
                continue
            if self._open_position(sig):
                opened += 1
        return opened

    def _open_position(self, sig) -> bool:
        # size against capital STILL AVAILABLE today, not the full day's
        # allowance - otherwise several trades that each respect the per-trade
        # cap can together exceed deploy_today
        qty = self.risk.size_for(sig, self.portfolio)
        position_id = f"{sig.symbol}-{sig.strategy}-{sig.bar_time.value}"
        if isinstance(self.adapter, PaperBroker):
            self.adapter.update_quotes({sig.symbol: sig.entry_ref})
        try:
            order = self.orders.market_entry(sig, qty, position_id)
        except Exception as exc:
            self.events.emit("error", where="entry", symbol=sig.symbol,
                             detail=str(exc))
            return False
        fill = order.avg_fill_price or sig.entry_ref
        pos = Position(
            position_id=position_id, symbol=sig.symbol, strategy=sig.strategy,
            timeframe=sig.timeframe, quantity=qty, entry_price=fill,
            entry_ts=now_iso(), stop=sig.stop, initial_stop=sig.stop,
            target=sig.target, target2=sig.target2,
            partial_fraction=sig.spec.partial_fraction,
            trail_mode=sig.spec.trail, open_quantity=qty,
            session=sig.session, last_price=fill)
        # ATR at entry for chandelier trailing (from the signal's frame proxy:
        # recompute cheaply from the risk geometry is not possible, so read it
        # off the prepared frame via the orchestrator-provided stop distance)
        pos.atr_at_entry = max(sig.entry_ref - sig.stop, 0.0) / \
            max(self.params.atr_stop_multiplier, 1e-9)
        self.portfolio.add_position(pos)
        self.events.emit("position", action="open", symbol=sig.symbol,
                         strategy=sig.strategy, price=fill, qty=qty,
                         stop=sig.stop)
        return True

    # -------------------------------------------------------- square-off

    def _squareoff_all(self, reason: str, timeframe: str) -> None:
        for pos in list(self.portfolio.open_positions()):
            self._squareoff_position(pos, timeframe, reason)

    def _squareoff_position(self, pos: Position, timeframe: str,
                            reason: str) -> None:
        frame = self.state.history(pos.symbol, pos.timeframe or timeframe)
        price = (float(frame["close"].iloc[-1]) if not frame.empty
                 else pos.last_price or pos.entry_price)
        self._execute_exit(pos, price, reason)

    # ------------------------------------------------------------- summary

    def end_of_day(self) -> dict:
        summary = daily_summary(self.portfolio, self.config.mode)
        path = write_summary(self.config.state_dir, summary)
        self.events.emit("health", phase="eod", summary=summary)
        logger.info("daily summary written: %s", path)
        return summary

    def dashboard(self) -> str:
        health = self.health.beat(True, True)
        return render_dashboard(self.portfolio, health, self.config.mode)

    def risk_summary(self) -> str:
        """The effective limits and current risk position, for startup."""
        L = self.config.risk
        s = self.risk.risk_state(self.portfolio)
        rows = [
            ("Deploy Today", L.deploy_today),
            ("Max Daily Loss", L.max_daily_loss),
            ("Max Capital Per Trade", L.max_per_trade),
            ("Current Open Risk", s.open_risk),
            ("Reserved Pending Risk", s.reserved_risk),
            ("Remaining Portfolio Risk", s.remaining),
        ]
        width = 64
        out = ["", "=" * width, " CAPITAL & RISK BUDGET".ljust(width),
               "=" * width]
        for label, value in rows:
            out.append(f" {label:<32} Rs {value:>12,.0f}")
        out.append("=" * width)
        return "\n".join(out)


def _time(hour: int, minute: int):
    from datetime import time
    return time(hour, minute)
