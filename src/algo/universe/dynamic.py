"""Dynamic trading universe - top-N by market cap, ranked by liquidity.

Builds the DAILY trading universe the scanner subscribes to:

    STEP 1  eligible NSE equities: the official NIFTY 500 constituent list
            (series EQ only) intersected with the live instrument master.
            The index list is equity-only by construction - ETFs, REITs,
            InvITs, preference shares, bonds, MF units, rights and suspended
            securities are excluded by the index methodology, and anything
            no longer in the instrument master is dropped as inactive.
    STEP 2  market-cap ranking: NIFTY 500 membership IS the official
            free-float market-cap top-500 cut. (Per-symbol free-float values
            are not published in the constituent file, so ordering inside
            the pool falls back to the liquidity metric - documented in the
            report notes, exactly as configured.)
    STEP 3  cap the pool at ``mcap_pool_size`` (default 500).
    STEP 4  rank by YESTERDAY's traded value (close x volume from the last
            stored daily bar; falls back to the trading timeframe's last
            session when no daily series exists). ``liquidity_metric`` may
            be "traded_value" (default), "turnover" (uses the daily bar's
            turnover column when the store has one) or "volume".
    STEP 5  select the top ``size`` (default 500).

The result is persisted VERSIONED under ``user_data/universe/`` as
``dynamic-YYYY-MM-DD.json`` plus a stable pointer file ``dynamic_current.txt``
(one symbol per line) so anything that reads a symbols file keeps working.
``load_or_build`` reuses today's file when it exists, so the daily pre-open
regeneration is idempotent and a restart never rebuilds mid-session.

Modular on purpose: ranking inputs are assembled into one metrics frame and
the selection is a pure function of it, so future criteria (sector caps,
volatility filters, ATR, spread) plug into ``rank_metrics`` without touching
scanner or engine code.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

from algo.core.logging import get_logger
from algo.trading.universe import UniverseReport

logger = get_logger("universe.dynamic")

LIQUIDITY_METRICS = ("traded_value", "turnover", "volume")


@dataclass(frozen=True)
class DynamicUniverseSpec:
    """Everything configurable about the daily universe."""

    #: final universe size (STEP 5)
    size: int = 500
    #: market-cap pool cut (STEP 3)
    mcap_pool_size: int = 500
    #: liquidity ranking metric (STEP 4)
    liquidity_metric: str = "traded_value"
    #: official constituent list (free-float market-cap top 500). A glob is
    #: allowed; the lexically newest match wins so monthly snapshots rotate in
    #: without a config change.
    mcap_source: str = "user_data/universe/ind_nifty500_*.csv"
    #: where versioned universes and the current pointer are written
    out_dir: str = "user_data/universe"
    #: the trading timeframe used for the daily-bar fallback
    timeframe: str = "15m"
    exclude: Sequence[str] = ()

    @classmethod
    def from_dict(cls, data) -> "DynamicUniverseSpec":
        from algo.core.config import from_dict as _from
        spec = _from(cls, data or {})
        if spec.liquidity_metric not in LIQUIDITY_METRICS:
            raise ValueError(f"liquidity_metric must be one of "
                             f"{LIQUIDITY_METRICS}, "
                             f"not {spec.liquidity_metric!r}")
        return spec


def load_mcap_metadata(spec: DynamicUniverseSpec) -> tuple[List[str], Dict[str, str]]:
    """Official EQ pool plus current industry labels, in file order."""
    pattern = Path(spec.mcap_source)
    candidates = sorted(pattern.parent.glob(pattern.name))
    if not candidates:
        raise FileNotFoundError(
            f"no market-cap constituent file matches {spec.mcap_source}")
    path = candidates[-1]
    symbols: List[str] = []
    sectors: Dict[str, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            series = (row.get("Series") or "").strip().upper()
            symbol = (row.get("Symbol") or "").strip().upper()
            if symbol and series == "EQ":
                symbols.append(symbol)
                sectors[symbol] = (row.get("Industry") or "UNKNOWN").strip() \
                    or "UNKNOWN"
    logger.info("market-cap pool: %d EQ constituents from %s",
                len(symbols), path.name)
    return symbols, sectors


def load_mcap_pool(spec: DynamicUniverseSpec) -> List[str]:
    """STEP 1+2 compatibility surface: EQ symbols in official file order."""
    return load_mcap_metadata(spec)[0]


def rank_metrics(store, symbols: Sequence[str],
                 spec: DynamicUniverseSpec) -> pd.DataFrame:
    """STEP 4 inputs: one row per symbol with the liquidity metric, from the
    PREVIOUS session's stored bars. Pure assembly - selection reads this."""
    rows = []
    for symbol in symbols:
        daily = store.read(symbol, "1d")
        value = None
        if not daily.empty:
            last = daily.iloc[-1]
            volume = float(last.get("volume") or 0.0)
            close = float(last.get("close") or 0.0)
            if spec.liquidity_metric == "turnover" \
                    and "turnover" in daily.columns:
                value = float(last["turnover"])
            elif spec.liquidity_metric == "volume":
                value = volume
            else:
                value = close * volume
        else:
            bars = store.read(symbol, spec.timeframe)
            if not bars.empty:
                last_day = bars["date"].dt.normalize().iloc[-1]
                session = bars[bars["date"].dt.normalize() == last_day]
                volume = float(session["volume"].sum())
                close = float(session["close"].iloc[-1])
                value = (volume if spec.liquidity_metric == "volume"
                         else close * volume)
        if value is not None and value > 0:
            rows.append({"symbol": symbol, "liquidity": value})
    if not rows:
        return pd.DataFrame(columns=["symbol", "liquidity"]).set_index("symbol")
    return pd.DataFrame(rows).set_index("symbol")


def build_dynamic_universe(store, spec: DynamicUniverseSpec,
                           instruments=None,
                           asof: Optional[date] = None) -> UniverseReport:
    """Run STEP 1-5 and return the selection with a full audit trail."""
    report = UniverseReport(tier="dynamic", target_size=int(spec.size))
    pool, sectors = load_mcap_metadata(spec)
    excluded = {s.upper() for s in spec.exclude}
    pool = [s for s in pool if s not in excluded]

    if instruments is not None:
        active, inactive = [], 0
        for symbol in pool:
            if instruments.token_for(symbol) is not None:
                active.append(symbol)
            else:
                inactive += 1
        report.dropped["not_in_instrument_master"] = inactive
        pool = active

    pool = pool[:int(spec.mcap_pool_size)]
    report.candidates = len(pool)
    report.notes.append(
        f"market-cap pool: official NIFTY500 constituents (free-float "
        f"market-cap top-500 cut), capped at {spec.mcap_pool_size}")

    metrics = rank_metrics(store, pool, spec)
    unmeasured = len(pool) - len(metrics)
    if unmeasured:
        report.dropped["no_liquidity_data"] = unmeasured
        report.notes.append(
            f"{unmeasured} pool symbol(s) have no stored bars to rank by - "
            "download daily data to include them")

    ranked = metrics.sort_values("liquidity", ascending=False)
    report.selected = list(ranked.index[:int(spec.size)])
    report.sectors = {symbol: sectors.get(symbol, "UNKNOWN")
                      for symbol in report.selected}
    report.notes.append(
        f"ranked by previous session's {spec.liquidity_metric}")
    if report.short_of_target:
        report.notes.append(
            f"{report.short_of_target} short of {spec.size} - "
            "insufficient ranked candidates")
    logger.info("%s", report.summary().replace("\n", " | "))
    return report


# ------------------------------------------------------------- persistence

def universe_path(spec: DynamicUniverseSpec, day: date) -> Path:
    return Path(spec.out_dir) / f"dynamic-{day.isoformat()}.json"


def pointer_path(spec: DynamicUniverseSpec) -> Path:
    return Path(spec.out_dir) / "dynamic_current.txt"


def persist(report: UniverseReport, spec: DynamicUniverseSpec,
            day: date) -> Path:
    """Versioned JSON + the stable one-symbol-per-line pointer file."""
    out = universe_path(spec, day)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_for": day.isoformat(),
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "spec": {"size": spec.size, "mcap_pool_size": spec.mcap_pool_size,
                 "liquidity_metric": spec.liquidity_metric,
                 "mcap_source": spec.mcap_source},
        "report": report.to_dict(),
        "symbols": list(report.selected),
    }
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    lines = [f"# dynamic universe {day.isoformat()} "
             f"({len(report.selected)} symbols)"] + list(report.selected)
    pointer_path(spec).write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("dynamic universe persisted: %s (%d symbols)",
                out.name, len(report.selected))
    return out


def load_universe(spec: DynamicUniverseSpec,
                  day: date) -> Optional[List[str]]:
    path = universe_path(spec, day)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        saved = payload.get("spec", {})
        expected = {"size": spec.size,
                    "mcap_pool_size": spec.mcap_pool_size,
                    "liquidity_metric": spec.liquidity_metric,
                    "mcap_source": spec.mcap_source}
        if any(saved.get(key) != value for key, value in expected.items()):
            logger.info("%s was built for a different universe spec - "
                        "rebuilding", path.name)
            return None
        symbols = [str(s).upper() for s in payload.get("symbols", [])]
        return symbols or None
    except Exception as exc:
        logger.warning("could not read %s (%s) - rebuilding", path.name, exc)
        return None


def load_or_build(store, spec: DynamicUniverseSpec, instruments=None,
                  day: Optional[date] = None) -> UniverseReport:
    """Today's universe: reuse the persisted version, else build and persist.

    Idempotent per day - the pre-open regeneration and a mid-session restart
    read the same file, so the universe cannot silently change intraday.
    """
    day = day or date.today()
    existing = load_universe(spec, day)
    if existing:
        _, sectors = load_mcap_metadata(spec)
        report = UniverseReport(tier="dynamic", target_size=int(spec.size),
                                selected=existing,
                                candidates=len(existing),
                                sectors={s: sectors.get(s, "UNKNOWN")
                                         for s in existing})
        report.notes.append(f"loaded from {universe_path(spec, day).name}")
        return report
    report = build_dynamic_universe(store, spec, instruments=instruments,
                                    asof=day)
    if report.selected:
        persist(report, spec, day)
    return report


class DailyUniverseRefresher:
    """Session-day refresh hook for the run loop: build once per day before
    the open, and hand the new list to the engine WITHOUT a restart."""

    def __init__(self, store, spec: DynamicUniverseSpec,
                 instruments=None) -> None:
        self.store = store
        self.spec = spec
        self.instruments = instruments
        self._served_day: Optional[date] = None

    def refresh_if_due(self, engine, source=None,
                       day: Optional[date] = None) -> Optional[List[str]]:
        """Returns the new symbol list when the universe changed, else None.

        Safe to call every loop iteration; it does real work at most once per
        calendar day. ``source`` (the streaming source) is resubscribed so
        the feed follows the universe.
        """
        day = day or engine.clock.now().date()
        if self._served_day == day:
            return None
        report = load_or_build(self.store, self.spec,
                               instruments=self.instruments, day=day)
        self._served_day = day
        if not report.selected:
            logger.warning("dynamic universe empty for %s - keeping the "
                           "current watchlist", day)
            return None
        current = list(engine.state.symbols)
        symbols = list(report.selected)
        # Metadata belongs to the daily universe snapshot even when the
        # selected symbols are unchanged.  Refresh it before the fast path so
        # updated classifications cannot leave sector-risk checks stale.
        engine.state.universe_report = report
        engine.state.sector_by_symbol = dict(report.sectors)
        if symbols == current:
            return None
        engine.marketdata.set_symbols(symbols)
        if source is not None and hasattr(source, "resubscribe"):
            source.resubscribe(symbols)
        logger.info("universe refreshed for %s: %d symbols (%+d)",
                    day, len(symbols), len(symbols) - len(current))
        return symbols
