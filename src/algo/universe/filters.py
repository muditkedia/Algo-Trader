"""Config-driven universe filter pipeline.

``build_filters(config)`` turns a plain config dict into an ordered list of the
generic Phase-1 filters. Every threshold is supplied by config - nothing is
hardcoded - satisfying the "everything configurable" mandate. The metric column
names (``price``, ``avg_volume``, ``avg_traded_value``, ``market_cap``,
``sector``) are the ones the ``UniverseManager`` produces.

Recognized keys (all optional):
    blacklist              list[str]   drop these symbols
    whitelist              list[str]   keep only these symbols
    min_price / max_price  float       price band
    min_avg_volume         float       average daily volume floor
    min_avg_traded_value   float       average traded value floor (price*vol)
    min_market_cap         float       market-cap floor (when available)
    sectors                list[str]   keep only these sectors
    exclude_sectors        list[str]   drop these sectors
"""

from __future__ import annotations

from typing import List

from algo.universe.base import (
    CategoryFilter, ColumnRangeFilter, Filter, MembershipFilter,
)

# Metric column names produced by UniverseManager.metrics_frame.
COL_PRICE = "price"
COL_AVG_VOLUME = "avg_volume"
COL_AVG_TRADED_VALUE = "avg_traded_value"
COL_MARKET_CAP = "market_cap"
COL_SECTOR = "sector"


def build_filters(config: dict) -> List[Filter]:
    """Build the ordered filter pipeline from ``config`` (missing keys skipped)."""
    cfg = config or {}
    filters: List[Filter] = []

    if cfg.get("blacklist"):
        filters.append(MembershipFilter(cfg["blacklist"], exclude=True,
                                        name="blacklist"))
    if cfg.get("whitelist"):
        filters.append(MembershipFilter(cfg["whitelist"], name="whitelist"))

    if cfg.get("min_price") is not None or cfg.get("max_price") is not None:
        filters.append(ColumnRangeFilter(
            COL_PRICE, min_value=cfg.get("min_price"),
            max_value=cfg.get("max_price"), name="price_band"))
    if cfg.get("min_avg_volume") is not None:
        filters.append(ColumnRangeFilter(
            COL_AVG_VOLUME, min_value=cfg["min_avg_volume"], name="min_avg_volume"))
    if cfg.get("min_avg_traded_value") is not None:
        filters.append(ColumnRangeFilter(
            COL_AVG_TRADED_VALUE, min_value=cfg["min_avg_traded_value"],
            name="min_avg_traded_value"))
    if cfg.get("min_market_cap") is not None:
        filters.append(ColumnRangeFilter(
            COL_MARKET_CAP, min_value=cfg["min_market_cap"],
            name="min_market_cap"))
    if cfg.get("sectors") or cfg.get("exclude_sectors"):
        filters.append(CategoryFilter(
            COL_SECTOR, allowed=cfg.get("sectors"),
            blocked=cfg.get("exclude_sectors"), name="sector"))

    return filters
