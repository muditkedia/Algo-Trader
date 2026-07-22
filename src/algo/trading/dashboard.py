"""DashboardExporter - continuous read-only JSON snapshots for the dashboard.

PRESENTATION ONLY. This module observes engine state and writes JSON files; it
never places an order, never changes a decision, and nothing in the trading
path depends on it. Export failures are swallowed (logged) so a dashboard
problem can never affect trading.

Files written to ``config.dashboard_dir`` (default ``dashboard/dashboard_data``)
so a single static file server can serve the UI and its data together:

    system.json      engine/market/broker/feed/scanner/scheduler/recovery state
    scanner.json     what the scanner is doing right now + next-scan countdown
    positions.json   active trades incl. explainability + next planned action
    orders.json      recent order lifecycle
    portfolio.json   capital, exposure, realized/unrealized, open risk
    signals.json     potential + rejected opportunities with reasons
    timeline.json    chronological session feed
    performance.json today's trade statistics
    health.json      component health + host CPU/RAM (if psutil present)
    logs.json        latest engine messages (searchable in the UI)

Derived, human-readable fields (next planned action, entry explainability) are
computed HERE from the position and its frozen ExecutionSpec - the engine and
the strategies are untouched.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from algo.core.logging import get_logger

logger = get_logger("trading.dashboard")

#: Bump when a snapshot's field set changes incompatibly. The UI compares it
#: and shows an explicit warning rather than rendering superseded fields.
SCHEMA_VERSION = 2

#: human names for the strategy ids (display only)
STRATEGY_NAMES = {
    "orb_5m": "Opening Range Breakout (STRAT-01)",
    "vwap_15m": "VWAP Trend Continuation",
    "vwap_pullback_15m": "VWAP Pullback",
    "cpr_breakout_15m": "CPR Breakout",
    "orb_retest_5m": "ORB Retest Continuation (STRAT-02)",
    "pullback_15m": "EMA Pullback Continuation",
    "volexp_1h": "Bollinger Squeeze Breakout (1h)",
    "gapgo_15m": "Gap and Go",
    "insidebar_15m": "Inside Bar Breakout",
    "supertrend_15m": "Supertrend Continuation",
    "cpr_reversal_15m": "CPR Reversal",
    "nr7_intraday_15m": "NR7 Intraday",
    "__orphan__": "Orphaned (adopted at recovery)",
}

#: one-line description of each strategy's ENTRY trigger (display only)
ENTRY_RULES = {
    "orb_5m": "Buffered 5-minute opening-range break with same-slot RVOL, "
              "VWAP, EMA, regime and confidence confirmation",
    "vwap_15m": "Price reclaimed session VWAP on a session where >=60% of "
                "prior bars closed above VWAP",
    "vwap_pullback_15m": "Price tagged a rising session VWAP as support and "
                         "resumed above the prior bar's high",
    "cpr_breakout_15m": "Close crossed above the prior day's CPR top with "
                        "volume >= 1.5x its 20-bar average",
    "orb_retest_5m": "A sufficiently extended opening break retested and "
                     "held the boundary, then resumed on renewed RVOL",
    "pullback_15m": "In an EMA20>EMA50 uptrend, price dipped to the fast EMA "
                    "and closed back above it",
    "volexp_1h": "Bollinger bandwidth was compressed for 3 bars and price "
                 "closed above the upper band",
    "gapgo_15m": "Gap up >=2% held by the first bar, then price broke the "
                 "first bar's high in the opening phase",
    "insidebar_15m": "An inside bar formed inside its mother bar and price "
                     "closed above the mother bar's high",
    "supertrend_15m": "Supertrend(10,3) state flipped bullish",
    "cpr_reversal_15m": "On a wide-CPR (rotational) day, price rejected the "
                        "S1 floor pivot",
    "nr7_intraday_15m": "Yesterday was the narrowest range of 7 days and "
                        "price broke its high",
}


def _ist(ts) -> str:
    """UTC timestamp -> IST clock string. EVERY time shown on the dashboard
    goes through here: the operator watches an IST market, and mixing UTC and
    IST on one screen is a misreading waiting to happen."""
    if not ts:
        return ""
    try:
        return pd.Timestamp(ts).tz_convert("Asia/Kolkata").strftime("%H:%M:%S")
    except Exception:
        return str(ts)[11:19]


def _f(value, default=0.0) -> float:
    try:
        out = float(value)
        return out if out == out else default          # NaN guard
    except (TypeError, ValueError):
        return default


class DashboardExporter:
    """Writes the dashboard snapshots. Construct once; call ``export`` often."""

    #: how hard to retry a replace that lost the race to a reader. Five
    #: attempts from 10ms doubling = ~150ms worst case per file, which is
    #: far longer than a file send takes and short enough that the trading
    #: loop never notices.
    _replace_attempts = 5
    _replace_backoff_s = 0.01

    #: how long a directly-measured freshness report may be reused before the
    #: engine starts producing them (see _freshness)
    _fallback_ttl_s = 15.0

    def __init__(self, engine, directory: Optional[str] = None) -> None:
        self.engine = engine
        self.dir = Path(directory or getattr(engine.config, "dashboard_dir",
                                             "dashboard/dashboard_data"))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.started_ts = pd.Timestamp.now(tz="UTC").isoformat()
        self.scanner_activity: List[dict] = []
        self.last_export: Optional[str] = None
        #: consecutive skipped writes per file, surfaced on health.json so a
        #: PERSISTENT export problem is visible rather than merely logged
        self.write_failures: Dict[str, int] = {}
        #: (monotonic, FreshnessReport) measured by the exporter itself before
        #: the engine has run a cycle
        self._fallback_fresh = None
        self._sweep_stale_temps()

    def _sweep_stale_temps(self) -> None:
        """Remove staging files orphaned by a previous run (killed between
        write and replace). Harmless litter, but it accumulates silently."""
        try:
            mine = f".{os.getpid()}.tmp"
            for leftover in self.dir.glob("*.tmp"):
                if not leftover.name.endswith(mine):
                    leftover.unlink(missing_ok=True)
        except OSError:                       # never block startup on cleanup
            pass

    # ------------------------------------------------------------- helpers

    def _write(self, name: str, payload) -> bool:
        """Atomic write so the dashboard never reads a half-written file.

        ``schema`` lets the UI detect a snapshot left over from an older build
        and refuse to render it: a stale file with a superseded field set once
        showed pre-refactor capital numbers as if they were live.

        WINDOWS. ``os.replace`` is atomic on Windows but fails with
        ``PermissionError: [WinError 5] Access is denied`` while ANOTHER PROCESS
        holds the destination open - Python's ``open()`` does not pass
        FILE_SHARE_DELETE, so an ordinary reader blocks the replace. The
        dashboard's own static file server is that reader: the browser polls all
        ten snapshots every 2s, and ``logs.json`` (the largest payload, ~200 KB)
        is held open longest, which is why it failed by name.

        The collision lasts only as long as one file send, so the replace is
        RETRIED briefly rather than abandoned. Atomicity is preserved - it is
        still a single ``os.replace``, never a partial write to the live file.
        If every attempt loses the race the write is SKIPPED, not raised: each
        snapshot is a complete picture, so the next export (2s later) restores
        the file with nothing lost. Trading never waits on this.
        """
        body = {"generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
                "schema": SCHEMA_VERSION, "data": payload}
        path = self.dir / name
        # unique per process: a leftover temp from a killed run, or a second
        # exporter, must never be mistaken for this one's staging file
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(body, indent=1, default=str),
                           encoding="utf-8")
        except OSError as exc:
            logger.warning("dashboard: could not stage %s (%s)", name, exc)
            return False

        delay = self._replace_backoff_s
        for attempt in range(self._replace_attempts):
            try:
                os.replace(tmp, path)
                self.write_failures.pop(name, None)
                return True
            except PermissionError:
                # reader holds the destination - back off and try again
                if attempt < self._replace_attempts - 1:
                    time.sleep(delay)
                    delay *= 2
            except OSError as exc:
                logger.warning("dashboard: could not replace %s (%s)",
                               name, exc)
                break
        tmp.unlink(missing_ok=True)
        self.write_failures[name] = self.write_failures.get(name, 0) + 1
        logger.debug("dashboard: %s is being read right now - skipping this "
                     "snapshot (next export refreshes it)", name)
        return False

    def note_scanner(self, message: str, detail: str = "") -> None:
        """Record a scanner activity line (called by the engine's scan loop)."""
        now = pd.Timestamp.now(tz="UTC")
        self.scanner_activity.append({
            "ts": now.isoformat(), "time": _ist(now),
            "message": message, "detail": detail})
        self.scanner_activity = self.scanner_activity[-40:]

    def _events(self) -> List[dict]:
        try:
            return self.engine.events.read_day()
        except Exception:
            return []

    # -------------------------------------------------------------- export

    # ----------------------------------------------------------- fast tier

    def _market_timing(self) -> dict:
        """Market state and the countdowns an operator actually needs.

        The wall clock is not one of them - the operator's own screen already
        shows the time. What cannot be read anywhere else is how long until the
        next candle closes (when the scanner next acts) and how long until
        square-off (when every intraday position is closed regardless).
        """
        e = self.engine
        clock = e.clock
        now = clock.now()
        market_open = clock.is_open(now)
        session_day = clock.is_session_day(now.date())
        out = {
            "market_open": market_open,
            "status": ("OPEN" if market_open else
                       ("PRE-OPEN" if session_day
                        and now < clock.session_open(now.date())
                        else "CLOSED")),
            "session_date": now.date().isoformat(),
            "squareoff_time": clock.squareoff.strftime("%H:%M"),
            "entry_cutoff_time": clock.entry_cutoff.strftime("%H:%M"),
            "timeframes": self._timeframes(),
            "next_candle": {}, "seconds_to_squareoff": None,
            "seconds_to_entry_cutoff": None, "seconds_to_open": None,
            "session_seconds_remaining": 0,
        }
        for tf in self._timeframes():
            try:
                closes = clock.next_bar_close(tf, at=now)
                out["next_candle"][tf] = max(
                    0, int((closes - now).total_seconds()))
            except Exception:
                out["next_candle"][tf] = None
        if session_day:
            squareoff = now.replace(hour=clock.squareoff.hour,
                                    minute=clock.squareoff.minute,
                                    second=0, microsecond=0)
            cutoff = now.replace(hour=clock.entry_cutoff.hour,
                                 minute=clock.entry_cutoff.minute,
                                 second=0, microsecond=0)
            out["seconds_to_squareoff"] = max(
                0, int((squareoff - now).total_seconds()))
            out["seconds_to_entry_cutoff"] = max(
                0, int((cutoff - now).total_seconds()))
            if not market_open and now < clock.session_open(now.date()):
                out["seconds_to_open"] = max(
                    0, int((clock.session_open(now.date())
                            - now).total_seconds()))
        if market_open:
            out["session_seconds_remaining"] = max(
                0, int((clock.session_close(now.date()) - now).total_seconds()))
        return out

    def _live(self) -> dict:
        """The one-second snapshot: prices, P&L, exposure, countdowns.

        Small by design - it is rewritten every second, so it carries only what
        genuinely changes at that rate. Every value comes from the same runtime
        objects the slow tier reads (risk_state, the portfolio, the clock), so
        the two tiers cannot disagree.
        """
        e = self.engine
        risk = e.risk.risk_state(e.portfolio)          # THE source of truth
        fresh = self._freshness()
        update = self._update_snapshot()
        lifecycle = update.get("state")
        active = lifecycle in {"UPDATE_IN_PROGRESS", "PARTIAL"}
        quote_ts = getattr(e, "quote_ts", None)
        quoted = getattr(e, "quote_prices", {}) or {}
        rows = []
        for pos in e.portfolio.open_positions():
            price = pos.last_price or pos.entry_price
            # the label must follow the VALUE, not merely the existence of a
            # quote: run_cycle re-marks from completed bars, so a stale entry
            # in the quote map would otherwise label a candle close as "live"
            source = "LTP" if quoted.get(pos.symbol) == price else "CANDLE"
            rows.append({
                "position_id": pos.position_id,
                "symbol": pos.symbol,
                "current_price": round(price, 2),
                "price_source": source,
                "quantity": pos.open_quantity,
                "value": round(price * pos.open_quantity, 2),
                # the position owns this arithmetic - restating it here is how
                # a panel starts disagreeing with the engine
                "pnl": round(pos.unrealized(price), 2),
                "pnl_pct": round((price / pos.entry_price - 1.0) * 100, 2)
                if pos.entry_price else 0.0,
                "stop": round(pos.stop, 2),
                "distance_to_stop_pct": round(
                    (price / pos.stop - 1.0) * 100, 2) if pos.stop else None,
            })
        return {
            "timing": self._market_timing(),
            "positions": rows,
            "position_count": len(rows),
            "portfolio_value": round(risk.portfolio_value, 2),
            "deployed_capital": round(risk.deployed_capital, 2),
            "available_capital": round(risk.available_capital, 2),
            "realized_pnl": round(risk.realized_pnl, 2),
            "unrealized_pnl": round(risk.unrealized_pnl, 2),
            "total_pnl": round(risk.realized_pnl + risk.unrealized_pnl, 2),
            "open_risk": round(risk.open_risk, 2),
            "remaining_risk_budget": round(risk.remaining, 2),
            # price provenance, so a stale mark can never read as a live one
            "quotes_live": bool(quoted),
            "quote_age_seconds": (None if quote_ts is None else round(
                (pd.Timestamp.now(tz="UTC") - quote_ts).total_seconds(), 1)),
            "data_status": (lifecycle if active else
                            (fresh.status if fresh is not None else
                             lifecycle or "UNKNOWN")),
            "data_headline": (update.get("headline", "") if active else
                              (fresh.headline() if fresh is not None else
                               update.get("headline", ""))),
            "freshness_status": (fresh.status if fresh is not None else None),
            "update_status": lifecycle,
            "update_progress": update,
            "data_age_seconds": self._data_age_seconds(fresh),
        }

    def _data_age_seconds(self, fresh) -> Optional[float]:
        """Age of the newest candle backing the displayed prices.

        Measured against the ENGINE clock, the same one the FRESH/STALE verdict
        uses. Reading wall time here instead let the two disagree - the header
        once read "FRESH - candle 2h 9m old", which is a contradiction, not a
        status.
        """
        if fresh is None or fresh.newest_bar is None:
            return None
        try:
            now = pd.Timestamp(self.engine.clock.now()).tz_convert("UTC")
        except Exception:
            now = pd.Timestamp.now(tz="UTC")
        return round((now - fresh.newest_bar).total_seconds(), 1)

    def export_live(self) -> None:
        """Write ONLY the fast snapshot. Never raises."""
        try:
            self._write("live.json", self._live())
        except Exception as exc:      # pragma: no cover - defensive by design
            logger.warning("dashboard: live tier not exported: %s", exc)

    def export(self, scanner_state: Optional[dict] = None) -> None:
        """Write every snapshot. Never raises - a dashboard failure must not
        touch trading.

        Each snapshot is built and written INDEPENDENTLY: one file that cannot
        be rendered or replaced must not cost the other nine. Previously a
        single failure aborted the rest of the export, so a locked ``logs.json``
        - written last, by luck - was the only casualty; the ordering was
        incidental, not a guarantee.
        """
        events = self._events()
        snapshots = (
            ("live.json", self._live),
            ("marketdata.json", self._data_quality),
            ("system.json", self._system),
            ("scanner.json", lambda: self._scanner(scanner_state)),
            ("positions.json", self._positions),
            ("orders.json", self._orders),
            ("portfolio.json", self._portfolio),
            ("signals.json", lambda: self._signals(events)),
            ("timeline.json", lambda: self._timeline(events)),
            ("performance.json", self._performance),
            ("health.json", lambda: self._health(events)),
            ("logs.json", lambda: self._logs(events)),
        )
        for name, build in snapshots:
            try:
                self._write(name, build())
            except Exception as exc:  # pragma: no cover - defensive by design
                logger.warning("dashboard: %s not exported (trading "
                               "unaffected): %s", name, exc)
        self.last_export = pd.Timestamp.now(tz="UTC").isoformat()

    # -------------------------------------------------------------- system

    def _system(self) -> dict:
        e = self.engine
        clock = e.clock
        now = clock.now()
        market_open = clock.is_open(now)
        halted = e.risk.tripped or e.risk.emergency_stop_requested()
        update = self._update_snapshot()
        return {
            "engine_running": True,
            "mode": e.config.mode,
            "live_armed": e.config.live_armed(),
            "market_open": market_open,
            "market_status": ("OPEN" if market_open else
                              ("PRE-OPEN" if clock.is_session_day(now.date())
                               and now < clock.session_open(now.date())
                               else "CLOSED")),
            "current_time_ist": now.strftime("%Y-%m-%d %H:%M:%S"),
            "session_date": e.portfolio.session_date,
            "broker": {"name": e.adapter.name, "is_live": e.adapter.is_live,
                       "connected": e.risk.error_streak == 0},
            # a watchlist existing proves nothing about the FEED - this must
            # reflect whether candles are actually arriving (D-038)
            "data_feed_connected": bool(e.state.symbols) and self._feed_ok(),
            "data_feed_status": self._market_data()["status"],
            "update_status": update.get("state"),
            "update_progress": update,
            "scanner_status": ("HALTED" if halted else
                               ("SCANNING" if market_open else "IDLE")),
            "scheduler_status": "ACTIVE" if market_open else "IDLE",
            "recovery_status": "COMPLETE",
            "trading_halted": halted,
            "halt_reason": e.risk.trip_reason,
            "started_at": self.started_ts,
            "squareoff_time": clock.squareoff.strftime("%H:%M"),
            "entry_cutoff_time": clock.entry_cutoff.strftime("%H:%M"),
        }

    # --------------------------------------------------------- diagnostics

    def _token_mapping(self) -> dict:
        """Symbol -> token resolution for the whole watchlist, in one figure.

        "97 / 99 resolved" tells the operator at a glance whether market data
        can work at all. It comes from the engine's authoritative instrument
        map - the same object the data provider and broker adapter resolve
        through - so this panel cannot disagree with what trading actually sees.
        """
        try:
            mapping = self.engine.mapping_report()
        except Exception:                 # observation must never raise
            mapping = None
        if mapping is None:
            return {"applicable": False, "resolved": 0,
                    "total": len(self.engine.state.symbols), "unresolved": 0,
                    "examples": [], "master_loaded": False,
                    "status": "OFFLINE",
                    "summary": "No market-data provider wired - trading on "
                               "stored candles only."}
        data = mapping.to_dict()
        data["applicable"] = True
        data["status"] = ("OK" if mapping.ok else
                          ("NO MASTER" if not mapping.master_loaded
                           else "PARTIAL" if mapping.resolved_count
                           else "FAILED"))
        return data

    def _timeframes(self) -> list:
        """The engine's resolved live timeframes (strategy-declared)."""
        return list(getattr(self.engine, "timeframes", None)
                    or getattr(self.engine.state, "timeframes", None)
                    or self.engine.config.timeframes or ["15m"])

    def _primary_timeframe(self) -> str:
        return self._timeframes()[0]

    def _update_snapshot(self) -> dict:
        """Read engine-owned scheduler lifecycle data, if available."""
        try:
            return self.engine.update_snapshot(self._primary_timeframe()) or {}
        except Exception:              # observation must never raise
            return {}

    def _feed_ok(self) -> bool:
        """True when the last pass actually served the watchlist."""
        try:
            return self.engine.state.data_ok(self._primary_timeframe())
        except Exception:
            return True            # unknown is not the same as broken

    def _freshness(self):
        """The engine's latest FreshnessReport for the primary timeframe.

        Falls back to measuring it directly when the engine has not run a cycle
        yet (startup, or an export between cycles). "Unknown" is an honest
        answer but a poor one when the age is sitting there to be measured -
        and startup is exactly when an operator looks at the panel.
        """
        tf = self._primary_timeframe()
        report = (getattr(self.engine, "freshness", None) or {}).get(tf)
        if report is not None:
            self._fallback_fresh = None      # the engine is producing them now
            return report
        if self._update_snapshot().get("state") in {
                "UPDATE_IN_PROGRESS", "PARTIAL"}:
            # No completed report exists yet. Measuring here would recreate
            # the exact transient stale diagnosis the engine suppresses.
            return None
        assess = self.engine.state.freshness
        # The fallback reads every watchlist symbol's newest bar, so it is
        # O(universe) - fine once at startup, ruinous at the fast tier's
        # cadence: with 2000 symbols it cost 533 ms per call, which is exactly
        # the scaling the fast tier is supposed to avoid. Compute it at most
        # once per _fallback_ttl_s until the engine takes over.
        now = time.monotonic()
        cached = getattr(self, "_fallback_fresh", None)
        if cached is not None and now - cached[0] < self._fallback_ttl_s:
            return cached[1]
        try:
            report = assess(tf, clock=self.engine.clock)
        except Exception:            # observation must never raise
            return None
        self._fallback_fresh = (now, report)
        return report

    def _data_quality(self) -> dict:
        """The MARKET DATA panel: feed quality at a glance (§12).

        Every figure is read from MarketState - the same object the engine
        trades on - so this panel cannot drift from what the engine is actually
        working with. It computes nothing itself: freshness, symbol health and
        the fetch verdict all have exactly one implementation, and it is not
        here.
        """
        e = self.engine
        tf = self._primary_timeframe()
        fresh = self._freshness()
        update = self._update_snapshot()
        lifecycle = update.get("state")
        active = lifecycle in {"UPDATE_IN_PROGRESS", "PARTIAL"}
        mapping = self._token_mapping()
        refresh = e.state.diagnosis(tf)
        success = e.state.last_update.get(tf)
        configured = len(e.state.symbols)
        out = {
            "timeframe": tf,
            "configured_universe": configured,
            "resolved_symbols": (mapping.get("resolved", 0)
                                 if mapping.get("applicable") else configured),
            "mapping_applicable": bool(mapping.get("applicable")),
            "live_symbols": 0, "fresh_symbols": 0, "missing_symbols": 0,
            "stale_symbols": 0,
            "failed_fetches": int(refresh.get("blocked", 0)),
            "last_successful_update": (None if success is None
                                       else success.isoformat()),
            "last_successful_update_ist": _ist(success),
            "feed_status": "PENDING",
            "freshness_status": None,
            "update_status": lifecycle,
            "update_progress": update,
            "pending_symbols": int(update.get("pending", 0)),
            "headline": refresh.get("headline", ""),
            "stale_examples": [], "missing_examples": [],
            "worst_bars_behind": 0, "newest_bar": None, "expected_bar": None,
        }
        if fresh is not None:
            data = fresh.to_dict()
            out.update({
                "live_symbols": data["live"],
                "fresh_symbols": data["fresh"],
                "missing_symbols": data["missing"],
                "stale_symbols": data["stale"],
                "feed_status": data["status"],
                "freshness_status": data["status"],
                "headline": data["headline"],
                "stale_examples": data["stale_symbols"][:10],
                "missing_examples": data["missing_symbols"][:10],
                "worst_bars_behind": data["worst_bars_behind"],
                "newest_bar": data["newest_bar"],
                "expected_bar": data["expected_bar"],
                "market_open": data["market_open"],
            })
        if active:
            out["feed_status"] = lifecycle
            out["headline"] = update.get("headline", "")
            out["pending_symbols"] = int(update.get("pending", 0))
        elif out["failed_fetches"]:
            out["feed_status"] = "UNABLE TO FETCH"
        return out

    def _market_data(self) -> dict:
        """Why market data did or did not move on the last pass.

        Reports the FETCH, distinguishing "no new candles" from "unable to
        fetch candles" so the operator reads the reason instead of inferring
        it from an absence of rows. Every verdict is MarketState's own; the
        dashboard picks which to show and adds nothing.
        """
        state = self.engine.state
        states = {tf: state.diagnosis(tf) for tf in self._timeframes()}
        blocked = [s for s in states.values() if not s.get("ok", True)]
        primary = states.get(self._primary_timeframe()) or \
            (next(iter(states.values())) if states else None)
        update = self._update_snapshot()
        lifecycle = update.get("state")
        if lifecycle in {"UPDATE_IN_PROGRESS", "PARTIAL"}:
            return {
                "status": lifecycle,
                "headline": update.get("headline", ""),
                "reasons": {}, "examples": [], "timeframes": states,
                "update_progress": update,
                "pipeline": self._pipeline(),
            }
        if primary is None:
            return {"status": "PENDING", "headline": "No pass yet.",
                    "timeframes": {}}
        if blocked:
            worst = blocked[0]
            return {"status": "UNABLE TO FETCH",
                    "headline": worst.get("headline", ""),
                    "reasons": worst.get("reasons", {}),
                    "examples": worst.get("examples", []),
                    "timeframes": states}
        return {"status": ("OFFLINE" if primary.get("offline") else "OK"),
                "headline": primary.get("headline", ""),
                "reasons": {}, "examples": [], "timeframes": states,
                "update_progress": update,
                # the pipeline's own numbers: queue depth, rate headroom and
                # per-timeframe due state, so the operator can see WHY data is
                # or is not arriving rather than only that it did not
                "pipeline": self._pipeline()}

    def _pipeline(self) -> dict:
        """Scheduler/queue/rate-limiter state, straight from the service."""
        try:
            return self.engine.marketdata.snapshot(self.engine.clock.now())
        except Exception:              # observation must never raise
            return {}

    # ------------------------------------------------------------- scanner

    def _scanner(self, state: Optional[dict]) -> dict:
        e = self.engine
        now = e.clock.now()
        strategies = [s.name for s in e.strategies]
        try:
            countdown = int(e.scheduler.seconds_until_next(now))
        except Exception:
            countdown = None
        close = e.clock.session_close(now.date())
        remaining = max(0, int((close - now).total_seconds())) \
            if e.clock.is_open(now) else 0
        try:
            from algo.strategies.library import ALL_STRATEGIES
            registered = len(ALL_STRATEGIES)
        except Exception:
            registered = len(strategies)
        report = e.state.universe_report
        return {
            "token_mapping": self._token_mapping(),
            "market_data": self._market_data(),
            "watchlist_size": len(e.state.symbols),
            "universe_size": len(e.state.symbols),
            "universe_tier": (report.tier if report else "explicit list"),
            "universe_target": (report.target_size if report
                                else len(e.state.symbols)),
            "registered_strategies": registered,
            "scanning_strategies": len(strategies),
            "strategies_enabled": len(strategies),
            "strategy_ids": strategies,
            "strategy_names": [STRATEGY_NAMES.get(s, s) for s in strategies],
            "timeframes": list(self._timeframes()),
            "headline": (f"Looking for opportunities across "
                         f"{len(e.state.symbols)} stocks using "
                         f"{len(strategies)} strategies."),
            "next_scan_seconds": countdown,
            "session_seconds_remaining": remaining,
            "progress": (state or {}).get("progress"),
            "current_activity": (state or {}).get("current",
                                                  "Waiting for next completed "
                                                  "candle."),
            "activity": list(reversed(self.scanner_activity)),
        }

    # ----------------------------------------------------------- positions

    def _next_action(self, pos, spec) -> str:
        """One-line statement of what the engine will do next."""
        return self._execution_plan(pos, spec)["next_action"]

    def _execution_plan(self, pos, spec) -> dict:
        """The DETERMINISTIC execution plan for one position (§7).

        Not a narrative and not a guess: the triggers below are enumerated in
        the same order ``TradeManager.manage`` evaluates them, from the same
        frozen ExecutionSpec and the same live Position fields, so the panel
        states what the engine will actually do rather than a parallel
        description of what it is supposed to do.

        Order (TradeManager.manage): stop first (pessimistic same-bar) ->
        target/partial -> intraday square-off -> trailing ratchet -> hold.
        """
        squareoff = self.engine.clock.squareoff.strftime("%H:%M")
        price = pos.last_price or pos.entry_price
        triggers: List[dict] = []

        if spec is None or pos.strategy == "__orphan__":
            return {
                "state": "Unmanaged (adopted at recovery)",
                "next_action": "Square off immediately - no strategy owns "
                               "this position.",
                "triggers": [{
                    "condition": "on the next cycle",
                    "action": "Square off at market",
                    "kind": "squareoff"}],
                "trailing": "n/a", "squareoff_time": squareoff,
            }

        # 1) stop - always first, evaluated against the bar's low/open
        stop_reason = ("breakeven stop" if pos.partial_done
                       and ((pos.stop >= pos.entry_price) if pos.is_long
                            else (pos.stop <= pos.entry_price))
                       else ("trailing stop" if pos.trailed else "initial stop"))
        stop_verb = "down" if pos.is_long else "up"
        triggers.append({
            "condition": f"price trades {stop_verb} to Rs {pos.stop:,.2f}",
            "action": f"Exit the full remaining {pos.open_quantity:g} shares "
                      f"({stop_reason})",
            "kind": "stop", "level": round(pos.stop, 2)})

        # 2) target / partial
        if pos.target and not pos.partial_done:
            target_verb = "up" if pos.is_long else "down"
            if spec.partial_fraction > 0:
                pct = int(spec.partial_fraction * 100)
                qty = pos.open_quantity * spec.partial_fraction
                triggers.append({
                    "condition": f"price trades {target_verb} to Rs {pos.target:,.2f} "
                                 f"(target 1)",
                    "action": f"Book {pct}% ({qty:g} shares), then move the "
                              f"stop to breakeven Rs {pos.entry_price:,.2f}",
                    "kind": "partial", "level": round(pos.target, 2)})
            else:
                triggers.append({
                    "condition": f"price trades {target_verb} to Rs {pos.target:,.2f} "
                                 f"(target)",
                    "action": "Exit the full position",
                    "kind": "target", "level": round(pos.target, 2)})
        if pos.partial_done and pos.target2:
            triggers.append({
                "condition": f"price trades up to Rs {pos.target2:,.2f} "
                             f"(target 2)",
                "action": "Exit the remainder",
                "kind": "target", "level": round(pos.target2, 2)})

        # 3) square-off - unconditional for intraday
        if spec.intraday:
            triggers.append({
                "condition": f"the clock reaches {squareoff}",
                "action": "Square off at market regardless of P&L, target or "
                          "trailing state",
                "kind": "squareoff", "level": None})

        # 4) trailing ratchet
        if spec.trail == "chandelier":
            trailing = ("Chandelier: the stop ratchets up by ATR once in "
                        "profit, and never widens")
            triggers.append({
                "condition": "price closes higher and the ATR trail rises "
                             "above the current stop",
                "action": "Raise the stop (ratchet only - it is never widened)",
                "kind": "trail", "level": None})
        elif spec.trail == "column":
            trailing = (f"Level trail: the stop follows the "
                        f"{spec.trail_col} line upward only")
            triggers.append({
                "condition": f"the {spec.trail_col} level rises above the "
                             f"current stop",
                "action": "Raise the stop to that level",
                "kind": "trail", "level": None})
        else:
            trailing = "None - this strategy does not trail"

        # state + the single most immediate action
        if pos.partial_done:
            state = "Managing remainder (stop at breakeven)"
        elif pos.trailed:
            state = "Trailing stop active"
        elif pos.target:
            state = "Holding - waiting for target 1"
        else:
            state = "Holding - waiting for the stop or square-off"

        if pos.target and not pos.partial_done and spec.partial_fraction > 0:
            nxt = (f"Book {int(spec.partial_fraction * 100)}% at "
                   f"Rs {pos.target:,.2f}, then move the stop to breakeven "
                   f"Rs {pos.entry_price:,.2f}.")
        elif pos.partial_done and pos.target2:
            nxt = (f"Run the remainder to Rs {pos.target2:,.2f}, stop at "
                   f"breakeven Rs {pos.stop:,.2f}, square off at {squareoff}.")
        elif pos.target:
            nxt = (f"Exit at Rs {pos.target:,.2f} or stop Rs {pos.stop:,.2f}; "
                   f"square off at {squareoff}.")
        elif spec.trail != "none":
            nxt = (f"Trail the stop upward from Rs {pos.stop:,.2f}; exit on "
                   f"the trailed stop or square off at {squareoff}.")
        else:
            nxt = (f"Hold to the stop at Rs {pos.stop:,.2f}; square off at "
                   f"{squareoff}.")

        return {"state": state, "next_action": nxt, "triggers": triggers,
                "trailing": trailing, "squareoff_time": squareoff,
                "current_price": round(price, 2)}

    def _explain(self, pos) -> dict:
        """Why this trade was executed. The position EXISTS, therefore every
        pre-trade gate returned approve - each is reported with the concrete
        limit it was checked against."""
        L = self.engine.config.risk
        risk_amount = abs(pos.entry_price - pos.initial_stop) * pos.quantity
        return {
            "entry_trigger": ENTRY_RULES.get(pos.strategy,
                                             "Strategy entry condition met"),
            "volume_confirmation": (
                "Required and satisfied (>=1.5x 20-bar average)"
                if pos.strategy in ("orb_5m", "cpr_breakout_15m")
                else "Not part of this strategy's entry rule"),
            "trend_confirmation": (
                "Entry rule includes its own trend/state condition"),
            "risk_approval": (
                f"Approved - risk Rs {risk_amount:,.0f} <= per-trade limit "
                f"Rs {L.max_daily_loss:,.0f} portfolio risk budget"),
            "capital_approval": (
                f"Approved - stake Rs {pos.entry_price * pos.quantity:,.0f} "
                f"within exposure cap Rs "
                f"{L.max_per_trade:,.0f} (half the day's capital)"),
            "duplicate_check": (
                f"Passed - no existing position in {pos.symbol}"),
            "daily_loss_check": (
                f"Passed - day P&L above the Rs {L.max_daily_loss:,.0f} "
                f"loss limit"),
            "reason_executed": (
                f"{STRATEGY_NAMES.get(pos.strategy, pos.strategy)} triggered "
                f"on {pos.symbol} and all account risk gates approved the "
                f"entry."),
        }

    def _positions(self) -> dict:
        e = self.engine
        rows = []
        timing = self._market_timing()
        quoted = getattr(e, "quote_prices", {}) or {}
        for pos in e.portfolio.open_positions():
            spec = e.specs.get(pos.strategy)
            price = pos.last_price or pos.entry_price
            pnl = pos.unrealized(price)          # the position owns this
            pnl_pct = ((price / pos.entry_price - 1.0) * 100
                       if pos.entry_price else 0.0)
            plan = self._execution_plan(pos, spec)
            state = plan["state"]
            rows.append({
                # ---- execution plan (§7) + square-off countdown (§9)
                "execution_plan": plan,
                "triggers": plan["triggers"],
                # NOT "trailing": that key is the position's boolean state
                # below, and colliding on it silently dropped this text
                "trailing_rule": plan["trailing"],
                "squareoff_time": plan["squareoff_time"],
                "seconds_to_squareoff": timing["seconds_to_squareoff"],
                "intraday": bool(spec.intraday) if spec else True,
                # ---- price provenance: never show a mark without its source
                "price_source": "LTP"
                if quoted.get(pos.symbol) == price else "CANDLE",
                "position_id": pos.position_id,
                "symbol": pos.symbol,
                "strategy_id": pos.strategy,
                "strategy": STRATEGY_NAMES.get(pos.strategy, pos.strategy),
                "direction": pos.direction,
                "quantity": pos.open_quantity,
                "entry_price": round(pos.entry_price, 2),
                "current_price": round(price, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "stop_loss": round(pos.stop, 2),
                "initial_stop": round(pos.initial_stop, 2),
                "target_1": round(pos.target, 2) if pos.target else None,
                "target_2": round(pos.target2, 2) if pos.target2 else None,
                "partial_done": pos.partial_done,
                "trailing": pos.trailed,
                "entry_time": _ist(pos.entry_ts),
                "state": state,
                "next_action": plan["next_action"],
                "explainability": self._explain(pos),
                "headline": (f"Bought {pos.symbol} at Rs "
                             f"{pos.entry_price:,.2f}"),
            })
        return {"count": len(rows), "positions": rows}

    # -------------------------------------------------------------- orders

    def _orders(self) -> dict:
        e = self.engine
        rows = []
        for o in list(e.portfolio.orders.values())[-100:]:
            rows.append({
                "client_order_id": o.client_order_id,
                "broker_order_id": o.broker_order_id,
                "symbol": o.symbol, "side": o.side,
                "quantity": o.quantity, "type": o.order_type,
                "status": o.status, "intent": o.intent,
                "filled_quantity": o.filled_quantity,
                "avg_fill_price": round(_f(o.avg_fill_price), 2),
                "strategy": STRATEGY_NAMES.get(o.strategy, o.strategy),
                "reason": o.reason, "created": o.created_ts,
                "updated": o.updated_ts, "time": _ist(o.updated_ts),
            })
        rows.reverse()
        return {"count": len(rows), "orders": rows}

    # ----------------------------------------------------------- portfolio

    def _portfolio(self) -> dict:
        """Capital and risk EXACTLY as the trading engine sees them.

        Every number here comes from ``AccountRiskEngine.risk_state`` - the
        same call sizing and the entry gate use - so the dashboard cannot
        drift from the engine. Nothing is recomputed locally.
        """
        e = self.engine
        L = e.config.risk
        risk = e.risk.risk_state(e.portfolio)          # THE source of truth
        deployed = risk.deployed_capital
        return {
            "mode": e.config.mode,
            "deploy_today": L.deploy_today,
            # the live mark, not the static allowance - ONE definition,
            # shared with live.json via PortfolioRisk
            "portfolio_value": round(risk.portfolio_value, 2),
            "capital_base": L.deploy_today,
            "max_per_trade": L.max_per_trade,
            "deployed_capital": round(deployed, 2),
            "available_capital": round(risk.available_capital, 2),
            "exposure_pct": round(100 * deployed / L.deploy_today, 2)
            if L.deploy_today else 0.0,
            "realized_pnl": round(risk.realized_pnl, 2),
            "unrealized_pnl": round(risk.unrealized_pnl, 2),
            "total_pnl": round(risk.realized_pnl + risk.unrealized_pnl, 2),
            "open_positions": e.portfolio.open_count(),
            "max_open_positions": L.max_open_positions,
            "open_risk": round(risk.open_risk, 2),
            "max_daily_loss": L.max_daily_loss,
            # portfolio risk budget (observational only)
            "portfolio_open_risk": round(risk.open_risk, 2),
            "reserved_pending_risk": round(risk.reserved_risk, 2),
            "remaining_risk_budget": round(risk.remaining, 2),
            "risk_utilization_pct": round(risk.utilization_pct, 1),
            "realized_loss": round(risk.realized_loss, 2),
            "unrealized_loss": round(min(0.0, risk.unrealized_pnl), 2),
        }

    # ------------------------------------------------------------- signals

    def _signals(self, events: List[dict]) -> dict:
        potential, rejected = [], []
        for ev in events:
            if ev.get("type") == "signal":
                potential.append({
                    "ts": ev.get("ts"), "time": _ist(ev.get("ts")),
                    "symbol": ev.get("symbol"),
                    "strategy_id": ev.get("strategy"),
                    "strategy": STRATEGY_NAMES.get(ev.get("strategy"),
                                                   ev.get("strategy")),
                    "entry": _f(ev.get("entry")), "stop": _f(ev.get("stop")),
                    "target": _f(ev.get("target")),
                    "confidence": round(_f(ev.get("confidence")) * 100, 1),
                })
            elif ev.get("type") == "risk_block":
                rejected.append({
                    "ts": ev.get("ts"), "time": _ist(ev.get("ts")),
                    "symbol": ev.get("symbol"),
                    "strategy_id": ev.get("strategy"),
                    "strategy": STRATEGY_NAMES.get(ev.get("strategy"),
                                                   ev.get("strategy")),
                    "reason": ev.get("reason"),
                })
        waiting = ("Waiting for the next completed candle on "
                   f"{', '.join(self._timeframes())}.")
        return {"potential_count": len(potential),
                "rejected_count": len(rejected),
                "potential": list(reversed(potential))[:50],
                "rejected": list(reversed(rejected))[:50],
                "waiting_condition": waiting}

    # ------------------------------------------------------------ timeline

    def _timeline(self, events: List[dict]) -> dict:
        out = []
        for ev in events:
            t = ev.get("type")
            ts = str(ev.get("ts", ""))
            hhmm = _ist(ts)
            sym = ev.get("symbol", "")
            if t == "signal":
                text = (f"{sym} qualified "
                        f"{STRATEGY_NAMES.get(ev.get('strategy'), '')}")
                kind = "signal"
            elif t == "risk_block":
                text = f"{sym} rejected - {ev.get('reason')}"
                kind = "reject"
            elif t == "order":
                text = (f"{ev.get('side', '')} {ev.get('intent', '')} "
                        f"{sym} qty {ev.get('qty', '')} -> "
                        f"{ev.get('status', '')}").strip()
                kind = "order"
            elif t == "position":
                action = ev.get("action")
                if action == "open":
                    side = "BUY" if ev.get("direction") != "short" else "SELL"
                    text = (f"{side} filled {sym} @ Rs "
                            f"{_f(ev.get('price')):,.2f}")
                elif action == "close":
                    text = (f"{sym} closed ({ev.get('reason')}) @ Rs "
                            f"{_f(ev.get('price')):,.2f} | P&L Rs "
                            f"{_f(ev.get('pnl')):,.0f}")
                elif action == "partial":
                    text = (f"{sym} partial booked @ Rs "
                            f"{_f(ev.get('price')):,.2f}; stop -> breakeven")
                elif action == "trail":
                    text = (f"{sym} stop trailed to Rs "
                            f"{_f(ev.get('stop')):,.2f}")
                else:
                    text = f"{sym} {action}"
                kind = "position"
            elif t == "recovery":
                text = f"Recovery: {ev.get('action', 'reconciled')}"
                kind = "system"
            elif t == "error":
                text = f"ERROR in {ev.get('where')}: {ev.get('detail')}"
                kind = "error"
            elif t == "health" and ev.get("phase") == "startup":
                text = "Engine started - preflight complete"
                kind = "system"
            else:
                continue
            out.append({"time": hhmm, "text": text, "kind": kind, "ts": ts})
        return {"count": len(out), "events": list(reversed(out))[:200]}

    # ---------------------------------------------------------- performance

    def _performance(self) -> dict:
        trades = self.engine.portfolio.closed_trades
        pnls = [_f(t.get("pnl")) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        # the risk engine defines open risk; recomputing it here is exactly
        # the duplication that lets two panels report different numbers
        from algo.trading.risk import position_open_risk
        open_risk = sum(position_open_risk(p)
                        for p in self.engine.portfolio.open_positions())
        return {
            "trades": len(trades),
            "wins": len(wins), "losses": len(losses),
            "win_rate": round(100 * len(wins) / len(trades), 1)
            if trades else 0.0,
            "pnl": round(sum(pnls), 2),
            "largest_win": round(max(wins), 2) if wins else 0.0,
            "largest_loss": round(min(losses), 2) if losses else 0.0,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
            "open_risk": round(open_risk, 2),
            "closed_trades": [{
                "symbol": t.get("symbol"),
                "strategy": STRATEGY_NAMES.get(t.get("strategy"),
                                               t.get("strategy")),
                "entry": round(_f(t.get("entry_price")), 2),
                "exit": round(_f(t.get("exit_price")), 2),
                "quantity": _f(t.get("quantity")),
                "pnl": round(_f(t.get("pnl")), 2),
                "exit_reason": t.get("exit_reason"),
                "entry_ts": t.get("entry_ts"), "exit_ts": t.get("exit_ts"),
                "entry_time": _ist(t.get("entry_ts")),
                "exit_time": _ist(t.get("exit_ts")),
            } for t in reversed(trades)],
        }

    # -------------------------------------------------------------- health

    def _health(self, events: List[dict]) -> dict:
        e = self.engine
        cpu = ram = None
        try:                                   # optional dependency
            import psutil
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
        except Exception:
            pass
        errors = [ev for ev in events if ev.get("type") == "error"]
        market_open = e.clock.is_open()
        halted = e.risk.tripped or e.risk.emergency_stop_requested()
        def status(ok: bool, idle: bool = False) -> str:
            return "IDLE" if idle else ("OK" if ok else "DEGRADED")
        return {
            "cpu_percent": cpu, "ram_percent": ram,
            "metrics_available": cpu is not None,
            "components": {
                "engine": "HALTED" if halted else "OK",
                "scanner": status(True, idle=not market_open),
                "broker": status(e.risk.error_streak == 0),
                "data_feed": status(bool(e.state.symbols) and self._feed_ok()),
                "scheduler": status(True, idle=not market_open),
                "recovery": "OK",
            },
            "cycles": getattr(e.health, "cycles", 0),
            "errors_today": len(errors),
            "error_streak": e.risk.error_streak,
            "circuit_breaker_limit": e.config.risk.circuit_breaker_errors,
            "risk_tripped": e.risk.tripped,
            "trip_reason": e.risk.trip_reason,
            "last_export": self.last_export,
            # a single skipped write is a normal read collision; a file that
            # keeps losing the race is a real export problem and says so
            "export_failures": dict(self.write_failures),
            "export_degraded": any(n >= 3 for n in
                                   self.write_failures.values()),
        }

    # ---------------------------------------------------------------- logs

    def _logs(self, events: List[dict]) -> dict:
        rows = []
        for ev in events[-500:]:
            level = ("ERROR" if ev.get("type") == "error" else
                     ("WARN" if ev.get("type") in ("risk_block", "halt")
                      else "INFO"))
            detail = {k: v for k, v in ev.items() if k not in ("ts", "type")}
            rows.append({"time": _ist(ev.get("ts")), "level": level,
                         "source": ev.get("type", ""),
                         "message": json.dumps(detail, default=str)[:400]})
        rows.reverse()
        return {"count": len(rows), "logs": rows}
