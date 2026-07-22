"""Watchlist construction - WHICH symbols the session trades.

Separated from the market-data subsystem on purpose. "Who is in the universe"
is a trading-policy question that reads liquidity rankings and operator config;
"keep these symbols current" is a data question. Fusing them is what put
universe selection inside the old feed object, so a component that only wanted
to read candles dragged in the universe builder.

The market-data subsystem is handed a plain list of symbols and has no opinion
about where it came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from algo.core.logging import get_logger
from algo.data.store import MarketDataStore

logger = get_logger("trading.watchlist")


@dataclass
class Watchlist:
    symbols: List[str]
    #: UniverseReport when the list was built from a universe spec, else None
    report: Optional[object] = None
    source: str = "file"

    def __len__(self) -> int:
        return len(self.symbols)


def build_watchlist(config, store: Optional[MarketDataStore] = None,
                    timeframes: Optional[Sequence[str]] = None) -> Watchlist:
    """The session's symbols, from a universe spec or an explicit file.

    ``timeframes`` is the engine's resolved live set (derived from the enabled
    strategies). It is passed in rather than re-read from config so the
    watchlist cannot be judged on a timeframe the scanner does not run.

    A ``universe`` block builds a liquidity-ranked, tradeable universe of the
    configured size; otherwise the explicit ``symbols_file`` is used, filtered
    to symbols that actually have data so the watchlist never claims coverage
    it does not have. The filter falls back to the raw list when it would empty
    it - an empty watchlist is a worse failure than an optimistic one, and the
    freshness report names anything that turns out to have no bars.
    """
    store = store or MarketDataStore(config.store_dir)
    timeframes = list(timeframes or config.timeframes) or ["15m"]
    timeframe = timeframes[0]

    universe = dict(getattr(config, "universe", None) or {})
    dynamic = universe.get("tier") == "dynamic"
    if universe and not dynamic:
        from algo.trading.universe import UniverseSpec, build_universe
        spec = UniverseSpec.from_dict({"timeframe": timeframe,
                                       **config.universe})
        report = build_universe(store, spec)
        return Watchlist(symbols=list(report.selected), report=report,
                         source="universe")
    if dynamic:
            # daily data-driven universe: top-500 by market cap, ranked by
            # the previous session's liquidity (algo.universe.dynamic).
            # NEVER a silent fallback: failure or an empty selection is
            # stated loudly, with the static file named as the substitute.
            from algo.universe.dynamic import (
                DynamicUniverseSpec, load_or_build,
            )
            universe.pop("tier", None)
            try:
                spec = DynamicUniverseSpec.from_dict(
                    {"timeframe": timeframe, **universe})
                report = load_or_build(store, spec)
            except Exception as exc:
                logger.error(
                    "UNIVERSE: dynamic build FAILED (%s) - falling back to "
                    "static watchlist %s", exc, config.symbols_file)
                report = None
            if report is not None and report.selected:
                logger.info(
                    "UNIVERSE: Dynamic - %d symbols (top %d of the NIFTY500 "
                    "market-cap pool by previous session's %s)",
                    len(report.selected), spec.size, spec.liquidity_metric)
                return Watchlist(symbols=list(report.selected), report=report,
                                 source="dynamic")
            if report is not None:
                logger.error(
                    "UNIVERSE: dynamic build selected 0 symbols (%s) - "
                    "falling back to static watchlist %s",
                    "; ".join(report.notes) or "no ranked candidates",
                    config.symbols_file)
        # explicit fallback: the static file, stated above, never silent

    symbols = [s.strip().upper()
               for s in Path(config.symbols_file).read_text().splitlines()
               if s.strip() and not s.startswith("#")]
    have = set(store.symbols(timeframe))
    kept = [s for s in symbols if s in have]
    if symbols and not kept:
        logger.warning("none of the %d configured symbols have %s data yet - "
                       "using the list as written", len(symbols), timeframe)
    logger.info("UNIVERSE: Static watchlist - %d symbols from %s",
                len(kept or symbols), config.symbols_file)
    return Watchlist(symbols=kept or symbols, source="file")
