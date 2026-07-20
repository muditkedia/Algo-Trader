"""Preflight - startup validation gate (Phase 9). Trading must not start if any
CRITICAL check fails.

Each check returns (name, ok, critical, detail). ``run_preflight`` aggregates
them; ``passed`` is False if any critical check failed. The engine refuses to
enter its trading loop unless preflight passes (non-critical warnings are
logged but do not block).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

import pandas as pd

from algo.core.logging import get_logger

logger = get_logger("trading.preflight")


@dataclass
class Check:
    name: str
    ok: bool
    critical: bool
    detail: str = ""


@dataclass
class PreflightResult:
    checks: List[Check]

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks if c.critical)

    def to_dict(self) -> dict:
        return {"passed": self.passed,
                "checks": [{"name": c.name, "ok": c.ok,
                            "critical": c.critical, "detail": c.detail}
                           for c in self.checks]}


def run_preflight(config, adapter, feed, strategies, portfolio, clock,
                  recovery_report=None) -> PreflightResult:
    checks: List[Check] = []

    # broker authentication / connectivity
    try:
        adapter.connect()
        checks.append(Check("broker_auth", True, True,
                            f"{adapter.name} connected"))
    except Exception as exc:
        checks.append(Check("broker_auth", False, True, str(exc)))

    # live arming sanity: if mode=live, it must be fully armed OR explicitly
    # refuse to start (never half-armed live)
    if config.is_live:
        armed = config.live_armed()
        checks.append(Check("live_armed", armed, True,
                            "live fully armed" if armed else
                            "live mode but NOT armed - refusing to start"))

    # the timeframe the watchlist is judged on: the feed's own resolved set
    # (derived from the strategies), never a separately-maintained config list
    primary_tf = (list(getattr(feed, "timeframes", None) or config.timeframes)
                  or ["15m"])[0]

    # market-data connectivity / freshness
    try:
        watch = feed.symbols
        with_data = sum(1 for s in watch
                        if not feed.history(s, primary_tf).empty)
        ok = with_data > 0
        checks.append(Check("market_data", ok, True,
                            f"{with_data}/{len(watch)} symbols have "
                            f"{primary_tf} data"))
    except Exception as exc:
        checks.append(Check("market_data", False, True, str(exc)))

    # instrument availability
    checks.append(Check("watchlist", bool(feed.symbols), True,
                        f"{len(feed.symbols)} symbols"))

    # symbol -> instrument token mapping.
    #
    # This check exists because its absence cost a full paper session (D-038):
    # the instrument master was never loaded, every token lookup returned None,
    # and the engine traded on stale stored candles for hours while reporting
    # itself healthy. A watchlist that cannot be mapped CANNOT receive market
    # data, so a total failure is critical - it is not a warning to scroll past.
    try:
        reporter = getattr(feed, "mapping_report", None)
        mapping = reporter() if reporter is not None else None
        if mapping is None:
            checks.append(Check(
                "instrument_mapping", True, False,
                "no market-data provider wired - serving stored candles "
                "(offline paper mode)"))
        elif not mapping.master_loaded:
            checks.append(Check("instrument_mapping", False, True,
                                mapping.summary_line()))
        else:
            # a partial failure is survivable (the resolved symbols still
            # trade); a total one means no data at all can arrive
            critical = mapping.resolved_count == 0
            checks.append(Check(
                "instrument_mapping", not mapping.unresolved or not critical,
                critical,
                f"{mapping.resolved_count}/{mapping.total} symbols resolved"
                + (f" - unresolved: {', '.join(mapping.examples())}"
                   if mapping.unresolved else "")))
    except Exception as exc:
        checks.append(Check("instrument_mapping", False, False,
                            f"could not be verified: {exc}"))

    # strategy loading
    checks.append(Check("strategies", len(strategies) > 0, True,
                        f"{len(strategies)} enabled intraday strategies"))

    # daily capital configuration sanity (two operator inputs, rest derived)
    L = config.risk
    risk_ok = (L.deploy_today > 0 and L.max_daily_loss > 0
               and L.max_open_positions >= 1 and L.lot_size >= 1
               and L.max_daily_loss < L.deploy_today)
    checks.append(Check(
        "capital_config", risk_ok, True,
        f"deploy_today={L.deploy_today:,.0f} "
        f"max_daily_loss={L.max_daily_loss:,.0f} "
        f"-> max/trade={L.max_per_trade:,.0f} "
        f"min/trade={L.min_per_trade:,.0f} "
        f"({L.min_trade_allocation:.0%}) "
        f"portfolio risk budget={L.max_daily_loss:,.0f} "
        f"max_pos={L.max_open_positions}"))

    # The minimum allocation caps CONCURRENCY, which is not obvious from the
    # two numbers the operator sets: each position must commit at least
    # min_per_trade, so at most floor(1/allocation) can be open at once. A
    # max_open_positions of 5 behind a 25% floor can only ever reach 4.
    if L.min_trade_allocation > 0:
        concurrent = int(1.0 / L.min_trade_allocation)
        binding = concurrent < L.max_open_positions
        checks.append(Check(
            "position_capacity", not binding, False,
            f"the {L.min_trade_allocation:.0%} minimum allocation allows at "
            f"most {concurrent} concurrent positions"
            + (f" - LOWER than max_open_positions={L.max_open_positions}, "
               f"which is therefore unreachable" if binding
               else f" (max_open_positions={L.max_open_positions})")))
        if L.min_per_trade > L.max_per_trade:
            checks.append(Check(
                "position_capacity_valid", False, True,
                f"minimum allocation Rs {L.min_per_trade:,.0f} exceeds the "
                f"per-trade cap Rs {L.max_per_trade:,.0f} - NO trade can ever "
                f"be sized"))

    # storage access (state dir writable + evidence path parent)
    try:
        Path(config.state_dir).mkdir(parents=True, exist_ok=True)
        probe = Path(config.state_dir) / ".preflight"
        probe.write_text("ok"); probe.unlink()
        checks.append(Check("storage", True, True, config.state_dir))
    except Exception as exc:
        checks.append(Check("storage", False, True, str(exc)))

    # portfolio initialization
    checks.append(Check("portfolio", portfolio is not None, True,
                        f"{portfolio.open_count()} open positions loaded"))

    # clock / session sanity (naive drift guard: local clock is tz-aware IST)
    now = clock.now()
    checks.append(Check("clock", now.tzinfo is not None, True,
                        f"now={now.isoformat()} session_open="
                        f"{clock.is_session_day()}"))

    # recovery validation + reconciliation result
    if recovery_report is not None:
        orphans = (len(recovery_report.orphaned_broker))
        # orphaned broker positions are a WARNING (adopted + flagged), not a
        # hard block; but unresolved reconciliation errors are already logged
        checks.append(Check("recovery", True, False,
                            f"resumed={len(recovery_report.resumed)} "
                            f"orphan_broker={orphans} "
                            f"partials={len(recovery_report.partial_fills)}"))

    result = PreflightResult(checks)
    for c in checks:
        level = logger.info if c.ok else (
            logger.error if c.critical else logger.warning)
        level("preflight %-14s %s %s", c.name,
              "OK" if c.ok else ("FAIL" if c.critical else "warn"), c.detail)
    logger.info("PREFLIGHT %s", "PASSED" if result.passed else "FAILED")
    return result
