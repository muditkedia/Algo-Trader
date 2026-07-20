"""Production trading universe - configurable, liquidity-ranked, tradeable.

The engine must scan a universe the OPERATOR chooses, not whatever happens to
be in a text file. A universe is declared as a tier (a target size plus
quality floors) and built by ranking candidate symbols on liquidity and
keeping the best N that are actually tradeable today.

    dev        ~100 symbols   quick loops, low data cost
    paper      ~500 symbols   realistic breadth for paper trading
    production ~1000 symbols  the full liquid Indian equity market

Nothing is hardcoded to 1000: the tier table is a default, ``universe_size``
overrides it, and every floor is configurable.

Ranking uses **average daily traded value** (price x volume over a trailing
window) as the liquidity measure. It is deliberately the primary key: ADTV is
the metric that decides whether an order can actually be filled, and for
Indian equities it tracks market capitalisation closely. True market cap is
NOT in the candle store, so it is not claimed as an input - see
``UniverseReport.notes``.

Exclusions (all configurable): price band, ADTV floor, too little history
(recent listings), stale data (suspended/delisted names), an explicit
blacklist, and - decisively - any symbol without bars in the timeframe the
strategies trade, since it cannot be scanned at all.

Reuses the existing ``UniverseManager``/filter pipeline rather than
duplicating metric or filter logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

from algo.core.logging import get_logger
from algo.universe.manager import UniverseManager

logger = get_logger("trading.universe")

#: Default tier sizes. Overridden by ``universe_size``; never hardcoded
#: downstream.
TIER_SIZES: Dict[str, int] = {"dev": 100, "paper": 500, "production": 1000}

#: Candidate symbol pools, widest first. Files are plain one-symbol-per-line
#: lists (comments with '#'). Missing files are skipped.
DEFAULT_SOURCES = ("nifty500.txt", "microcap250.txt", "nifty100.txt")


@dataclass(frozen=True)
class UniverseSpec:
    """Everything that defines a tradeable universe. All configurable."""

    tier: str = "dev"
    #: explicit size; None -> TIER_SIZES[tier]
    size: Optional[int] = None
    sources: Sequence[str] = DEFAULT_SOURCES
    #: strategies' trading timeframe - a symbol without these bars is untradeable
    timeframe: str = "15m"
    #: liquidity window (sessions) on the daily store
    lookback: int = 20
    #: quality floors
    min_price: float = 20.0
    max_price: float = 1_000_000.0
    min_avg_traded_value: float = 1_00_00_000.0     # Rs 1 crore/day
    #: a symbol needs at least this many sessions of intraday history
    min_history_bars: int = 200
    #: ...and a bar within this many days, else treat as suspended/delisted
    max_stale_days: int = 10
    exclude: Sequence[str] = ()

    @property
    def target_size(self) -> int:
        return int(self.size if self.size is not None
                   else TIER_SIZES.get(self.tier, TIER_SIZES["dev"]))

    @classmethod
    def from_dict(cls, data) -> "UniverseSpec":
        from algo.core.config import from_dict as _from
        return _from(cls, data or {})


@dataclass
class UniverseReport:
    """Why the universe looks the way it does - printed at startup."""

    tier: str
    target_size: int
    selected: List[str] = field(default_factory=list)
    candidates: int = 0
    dropped: Dict[str, int] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.selected)

    @property
    def short_of_target(self) -> int:
        return max(0, self.target_size - self.size)

    def to_dict(self) -> dict:
        return {"tier": self.tier, "target_size": self.target_size,
                "size": self.size, "candidates": self.candidates,
                "dropped": self.dropped, "notes": self.notes,
                "short_of_target": self.short_of_target}

    def summary(self) -> str:
        lines = [f"universe[{self.tier}] {self.size}/{self.target_size} symbols "
                 f"from {self.candidates} candidates"]
        if self.dropped:
            lines.append("  dropped: " + ", ".join(
                f"{k}={v}" for k, v in sorted(self.dropped.items())))
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


def load_symbol_pool(sources: Sequence[str]) -> List[str]:
    """Union of the candidate lists, preserving first-seen order."""
    out: List[str] = []
    seen = set()
    for name in sources:
        path = Path(name)
        if not path.exists():
            logger.info("universe source not found, skipping: %s", name)
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            sym = line.strip().upper()
            if sym and not sym.startswith("#") and sym not in seen:
                seen.add(sym)
                out.append(sym)
    return out


def _liquidity(store, symbols: Sequence[str],
               spec: UniverseSpec) -> pd.DataFrame:
    """Per-symbol ``price`` and ``avg_traded_value`` (ADTV).

    Daily bars are the natural source and are used wherever they exist
    (reusing ``UniverseManager``). Many smaller names have intraday history
    but no daily series, so for those ADTV is derived from the TRADING
    timeframe by summing traded value per session and averaging the last
    ``lookback`` sessions - the same quantity, measured from the bars we
    actually have, instead of silently discarding the symbol.
    """
    manager = UniverseManager(store)
    daily = manager.metrics_frame(list(symbols), timeframe="1d",
                                  lookback=spec.lookback)
    daily = daily.dropna(subset=["price", "avg_traded_value"])
    daily = daily[["price", "avg_traded_value"]]

    missing = [s for s in symbols if s not in daily.index]
    rows = []
    for symbol in missing:
        bars = store.read(symbol, spec.timeframe)
        if bars.empty:
            continue
        per_session = (bars.assign(
            _value=bars["close"] * bars["volume"],
            _day=bars["date"].dt.normalize())
            .groupby("_day")["_value"].sum())
        if per_session.empty:
            continue
        rows.append({"symbol": symbol,
                     "price": float(bars["close"].iloc[-1]),
                     "avg_traded_value": float(
                         per_session.tail(spec.lookback).mean())})
    if rows:
        derived = pd.DataFrame(rows).set_index("symbol")
        daily = pd.concat([daily, derived])
    return daily.dropna(subset=["price", "avg_traded_value"])


def build_universe(store, spec: UniverseSpec,
                   pool: Optional[Sequence[str]] = None) -> UniverseReport:
    """Select the top ``spec.target_size`` tradeable symbols by liquidity."""
    report = UniverseReport(tier=spec.tier, target_size=spec.target_size)
    candidates = list(pool) if pool is not None else load_symbol_pool(spec.sources)
    excluded = {s.upper() for s in spec.exclude}
    candidates = [s for s in candidates if s not in excluded]
    report.candidates = len(candidates)
    if not candidates:
        report.notes.append("no candidate symbols found - check universe "
                            "sources")
        return report

    # 1) tradeability: must have enough recent bars in the TRADING timeframe
    tradeable, no_data, thin, stale = [], 0, 0, 0
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=spec.max_stale_days)
    for symbol in candidates:
        bars = store.read(symbol, spec.timeframe)
        if bars.empty:
            no_data += 1
            continue
        if len(bars) < spec.min_history_bars:
            thin += 1
            continue
        if pd.Timestamp(bars["date"].iloc[-1]) < cutoff:
            stale += 1                       # suspended / delisted / halted
            continue
        tradeable.append(symbol)
    report.dropped.update({"no_intraday_data": no_data,
                           "insufficient_history": thin,
                           "stale_or_suspended": stale})
    if not tradeable:
        report.notes.append(
            f"no candidate has {spec.timeframe} data - download it before "
            "trading this timeframe")
        return report

    # 2) liquidity metrics: daily bars where we have them, otherwise derived
    #    from the trading timeframe itself (see _liquidity)
    metrics = _liquidity(store, tradeable, spec)
    unmeasurable = len(tradeable) - len(metrics)
    if unmeasurable:
        # counted explicitly: a silent dropna here once removed 70% of the
        # universe (microcaps with intraday but no daily bars) invisibly
        report.dropped["no_liquidity_data"] = unmeasurable

    # 3) quality floors
    before = len(metrics)
    metrics = metrics[(metrics["price"] >= spec.min_price)
                      & (metrics["price"] <= spec.max_price)
                      & (metrics["avg_traded_value"]
                         >= spec.min_avg_traded_value)]
    report.dropped["below_quality_floor"] = before - len(metrics)

    # 4) rank by liquidity and take the best N
    ranked = metrics.sort_values("avg_traded_value", ascending=False)
    report.selected = list(ranked.index[:spec.target_size])

    if report.short_of_target:
        report.notes.append(
            f"{report.short_of_target} short of the {spec.target_size} target: "
            f"only {len(ranked)} candidates qualify with {spec.timeframe} "
            "data today - widen the sources or download more history")
    logger.info("%s", report.summary().replace("\n", " | "))
    return report
