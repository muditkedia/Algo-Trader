"""Configurable trading universe, strategy activation, and dashboard truth.

Covers the three review items:
  * the scan universe is configurable (dev/paper/production tiers) and ranked
    by liquidity, excluding untradeable names;
  * registered vs scanning strategy counts are correct and explained;
  * the dashboard reports EXACTLY the engine's runtime state, with real zeros
    rather than placeholders.
"""

import json

import pandas as pd
import pytest

from algo.core.enums import HoldingScope
from algo.data.store import MarketDataStore
from algo.strategies.library import ALL_STRATEGIES
from algo.trading.watchlist import build_watchlist
from algo.trading.config import TradingConfig
from algo.trading.dashboard import SCHEMA_VERSION
from algo.trading.engine import ProductionEngine, load_intraday_strategies
from algo.trading.universe import (
    TIER_SIZES, UniverseSpec, build_universe, load_symbol_pool,
)


# ------------------------------------------------------------- fixtures

def _bars(n_days, price, volume, tf_minutes=15, end=None, bars_per_day=25):
    """Synthetic daily+intraday bars ending today."""
    end = end or pd.Timestamp.now(tz="UTC").normalize()
    rows = []
    for d in range(n_days):
        day = end - pd.Timedelta(days=n_days - 1 - d)
        for b in range(bars_per_day):
            rows.append({
                "date": day + pd.Timedelta(minutes=tf_minutes * b),
                "open": price, "high": price * 1.01, "low": price * 0.99,
                "close": price, "volume": volume / bars_per_day})
    return pd.DataFrame(rows)


def _store(tmp_path, symbols):
    """symbols: {name: (price, daily_volume, n_days, stale_days)}"""
    store = MarketDataStore(tmp_path / "store")
    for name, (price, vol, n_days, stale) in symbols.items():
        end = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=stale)
        intraday = _bars(n_days, price, vol, end=end)
        store.write(name, "15m", intraday)
        daily = _bars(n_days, price, vol, tf_minutes=1440, end=end,
                      bars_per_day=1)
        store.write(name, "1d", daily)
    return store


# =============================================================== universe

def test_tier_sizes_are_configurable_defaults():
    assert TIER_SIZES == {"dev": 100, "paper": 500, "production": 1000}
    # nothing is hardcoded: an explicit size overrides the tier
    assert UniverseSpec(tier="production").target_size == 1000
    assert UniverseSpec(tier="production", size=250).target_size == 250
    assert UniverseSpec(tier="dev").target_size == 100


def test_universe_ranks_by_liquidity_and_takes_the_top_n(tmp_path):
    store = _store(tmp_path, {
        "BIG": (100.0, 5_000_000, 30, 0),      # ADTV 500m
        "MID": (100.0, 500_000, 30, 0),        # ADTV 50m
        "SMALL": (100.0, 50_000, 30, 0),       # ADTV 5m
    })
    spec = UniverseSpec(tier="dev", size=2, timeframe="15m",
                        min_history_bars=10, min_avg_traded_value=1_000_000)
    report = build_universe(store, spec, pool=["SMALL", "MID", "BIG"])
    assert report.selected == ["BIG", "MID"]           # most liquid first
    assert report.size == 2 and report.candidates == 3


def test_untradeable_names_are_excluded_with_reasons(tmp_path):
    store = _store(tmp_path, {
        "GOOD": (100.0, 5_000_000, 30, 0),
        "THIN_HISTORY": (100.0, 5_000_000, 2, 0),      # too few bars
        "SUSPENDED": (100.0, 5_000_000, 30, 60),       # no recent bars
        "PENNY": (2.0, 5_000_000, 30, 0),              # below price floor
        "ILLIQUID": (100.0, 100, 30, 0),               # below ADTV floor
    })
    spec = UniverseSpec(tier="dev", size=50, timeframe="15m",
                        min_history_bars=100, max_stale_days=10,
                        min_price=20.0, min_avg_traded_value=1_000_000)
    pool = ["GOOD", "THIN_HISTORY", "SUSPENDED", "PENNY", "ILLIQUID", "NODATA"]
    report = build_universe(store, spec, pool=pool)
    assert report.selected == ["GOOD"]
    d = report.dropped
    assert d["no_intraday_data"] == 1                  # NODATA
    assert d["insufficient_history"] == 1              # THIN_HISTORY
    assert d["stale_or_suspended"] == 1                # SUSPENDED
    assert d["below_quality_floor"] == 2               # PENNY + ILLIQUID


def test_symbol_without_the_trading_timeframe_is_never_selected(tmp_path):
    """A name we cannot scan must never enter the universe."""
    store = _store(tmp_path, {"GOOD": (100.0, 5_000_000, 30, 0)})
    spec = UniverseSpec(tier="dev", size=10, timeframe="5m",   # no 5m data
                        min_history_bars=10, min_avg_traded_value=0)
    report = build_universe(store, spec, pool=["GOOD"])
    assert report.selected == []
    assert "download it before trading" in " ".join(report.notes)


def test_report_states_how_far_short_of_target_it_is(tmp_path):
    store = _store(tmp_path, {"A": (100.0, 5_000_000, 30, 0)})
    spec = UniverseSpec(tier="production", timeframe="15m",
                        min_history_bars=10, min_avg_traded_value=0)
    report = build_universe(store, spec, pool=["A"])
    assert report.size == 1 and report.target_size == 1000
    assert report.short_of_target == 999
    assert any("short of the 1000 target" in n for n in report.notes)


def test_exclusions_are_honoured(tmp_path):
    store = _store(tmp_path, {"A": (100.0, 5_000_000, 30, 0),
                              "B": (100.0, 4_000_000, 30, 0)})
    spec = UniverseSpec(tier="dev", size=10, timeframe="15m",
                        min_history_bars=10, min_avg_traded_value=0,
                        exclude=("A",))
    assert build_universe(store, spec, pool=["A", "B"]).selected == ["B"]


def test_pool_loader_dedupes_and_skips_missing_files(tmp_path):
    f1 = tmp_path / "a.txt"; f1.write_text("# comment\nAAA\nBBB\n")
    f2 = tmp_path / "b.txt"; f2.write_text("BBB\nCCC\n")
    pool = load_symbol_pool([str(f1), str(f2), str(tmp_path / "nope.txt")])
    assert pool == ["AAA", "BBB", "CCC"]


def test_feed_builds_the_configured_universe(tmp_path):
    from algo.marketdata import MarketState
    _store(tmp_path, {"AAA": (100.0, 5_000_000, 30, 0),
                      "BBB": (100.0, 1_000_000, 30, 0)})
    (tmp_path / "pool.txt").write_text("AAA\nBBB\n")
    cfg = TradingConfig.from_dict({
        "store_dir": str(tmp_path / "store"), "timeframes": ["15m"],
        "symbols_file": str(tmp_path / "pool.txt"),
        "universe": {"tier": "dev", "size": 1,
                     "sources": [str(tmp_path / "pool.txt")],
                     "min_history_bars": 10, "min_avg_traded_value": 0}})
    feed = build_watchlist(cfg)
    assert feed.symbols == ["AAA"]                     # most liquid only
    assert feed.report.tier == "dev"


def test_explicit_symbols_file_still_works(tmp_path):
    """Backward compatible: no universe block -> the file is the watchlist."""
    from algo.marketdata import MarketState
    _store(tmp_path, {"AAA": (100.0, 5_000_000, 30, 0)})
    (tmp_path / "pool.txt").write_text("AAA\n")
    cfg = TradingConfig.from_dict({
        "store_dir": str(tmp_path / "store"), "timeframes": ["15m"],
        "symbols_file": str(tmp_path / "pool.txt")})
    feed = build_watchlist(cfg)
    assert feed.symbols == ["AAA"] and feed.report is None


# ==================================================== strategy activation

def test_registered_strategies_are_intraday_and_scan():
    intraday = [c for c in ALL_STRATEGIES
                if c.meta.holding_scope == HoldingScope.INTRADAY]
    assert len(intraday) == len(ALL_STRATEGIES)
    assert len(load_intraday_strategies()) == len(intraday)


def test_every_registered_strategy_scans():
    scanning = {s.name for s in load_intraday_strategies()}
    for cls in ALL_STRATEGIES:
        is_intraday = cls.meta.holding_scope == HoldingScope.INTRADAY
        assert (cls.meta.name in scanning) == (is_intraday and
                                               cls.meta.enabled), cls.meta.name


def test_disabled_strategies_would_be_excluded():
    """The loader gates on meta.enabled as well as scope."""
    import algo.trading.engine as engine_mod
    scanning_names = {s.name for s in engine_mod.load_intraday_strategies()}
    for cls in ALL_STRATEGIES:
        if not cls.meta.enabled:
            assert cls.meta.name not in scanning_names


def test_strategy_report_rows_match_the_registry():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "strategy_report", Path("scripts/strategy_report.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rows = mod.rows()
    assert len(rows) == len(ALL_STRATEGIES)
    assert sum(r["scanning"] for r in rows) == len(load_intraday_strategies())
    for r in rows:
        assert r["reason"]                              # always explained


# ============================================ dashboard reflects runtime state

def _engine(tmp_path, **cfg_kw):
    from tests.test_trading_integration import CraftedFeed
    (tmp_path / "syms.txt").write_text("AAA\n")
    base = {"mode": "paper", "state_dir": str(tmp_path / "state"),
            "store_dir": str(tmp_path / "store"),
            "symbols_file": str(tmp_path / "syms.txt"), "timeframes": ["15m"],
            "dashboard_dir": str(tmp_path / "dash"),
            "capital": {"deploy_today": 300_000, "max_daily_loss": 10_000}}
    base.update(cfg_kw)
    cfg = TradingConfig.from_dict(base)
    frame = _bars(3, 100.0, 100_000)
    feed = CraftedFeed({"AAA": frame}, ["15m"])
    return ProductionEngine(cfg, state=feed)


def _snapshot(engine, name):
    engine.exporter.export()
    path = engine.exporter.dir / f"{name}.json"
    return json.loads(path.read_text())


def test_dashboard_capital_matches_the_engine_exactly(tmp_path):
    engine = _engine(tmp_path)
    body = _snapshot(engine, "portfolio")
    data = body["data"]
    L = engine.config.risk
    state = engine.risk.risk_state(engine.portfolio)
    assert data["deploy_today"] == L.deploy_today == 300_000
    assert data["portfolio_value"] == L.portfolio_value
    assert data["max_per_trade"] == L.max_per_trade
    assert data["available_capital"] == pytest.approx(state.available_capital)
    assert data["deployed_capital"] == pytest.approx(state.deployed_capital)
    assert data["max_daily_loss"] == L.max_daily_loss
    # the retired pre-refactor fields must be gone
    for gone in ("max_capital", "stake_per_trade", "daily_loss_limit"):
        assert gone not in data


def test_zero_metrics_are_real_zeros_not_placeholders(tmp_path):
    """With an empty book these are KNOWN to be zero and must be present."""
    engine = _engine(tmp_path)
    data = _snapshot(engine, "portfolio")["data"]
    assert data["portfolio_open_risk"] == 0
    assert data["reserved_pending_risk"] == 0
    assert data["remaining_risk_budget"] == 10_000
    assert data["risk_utilization_pct"] == 0.0
    for key in ("portfolio_open_risk", "reserved_pending_risk",
                "remaining_risk_budget", "risk_utilization_pct"):
        assert data[key] is not None and not isinstance(data[key], str)


def test_snapshots_carry_the_schema_version(tmp_path):
    engine = _engine(tmp_path)
    for name in ("portfolio", "scanner", "system"):
        assert _snapshot(engine, name)["schema"] == SCHEMA_VERSION


def test_scanner_snapshot_reports_universe_and_strategy_counts(tmp_path):
    engine = _engine(tmp_path)
    data = _snapshot(engine, "scanner")["data"]
    assert data["registered_strategies"] == len(ALL_STRATEGIES)
    assert data["scanning_strategies"] == len(engine.strategies)
    assert data["universe_size"] == len(engine.feed.symbols)
