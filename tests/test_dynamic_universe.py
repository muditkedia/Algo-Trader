"""Dynamic universe - top-N market-cap pool ranked by yesterday's liquidity."""

import json
from datetime import date

import pandas as pd
import pytest

from algo.data.ohlcv import OHLCV_COLUMNS
from algo.data.store import MarketDataStore
from algo.universe.dynamic import (
    DailyUniverseRefresher, DynamicUniverseSpec, build_dynamic_universe,
    load_mcap_pool, load_or_build, persist, pointer_path, universe_path,
)

DAY = date(2026, 7, 22)


def _csv(tmp_path, rows):
    path = tmp_path / "ind_nifty500_2026-07.csv"
    lines = ["Company Name,Industry,Symbol,Series,ISIN Code"]
    lines += [f"{sym} Ltd.,Industry,{sym},{series},INE{sym}" for sym, series
              in rows]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _spec(tmp_path, **overrides):
    defaults = dict(size=3, mcap_pool_size=4,
                    mcap_source=str(tmp_path / "ind_nifty500_*.csv"),
                    out_dir=str(tmp_path / "universe"))
    defaults.update(overrides)
    return DynamicUniverseSpec.from_dict(defaults)


def _store_with_daily(tmp_path, traded):
    """A store whose last daily bar gives each symbol a known traded value."""
    store = MarketDataStore(tmp_path / "store")
    day = pd.Timestamp("2026-07-21", tz="UTC")
    for symbol, (close, volume) in traded.items():
        store.write(symbol, "1d", pd.DataFrame(
            [[day, close, close, close, close, volume]],
            columns=list(OHLCV_COLUMNS)))
    return store


def test_pool_keeps_only_eq_series(tmp_path):
    _csv(tmp_path, [("AAA", "EQ"), ("ETF1", "BE"), ("BBB", "EQ"),
                    ("REIT1", "RR")])
    pool = load_mcap_pool(_spec(tmp_path))
    assert pool == ["AAA", "BBB"]


def test_selection_ranks_by_yesterdays_traded_value(tmp_path):
    _csv(tmp_path, [("AAA", "EQ"), ("BBB", "EQ"), ("CCC", "EQ"),
                    ("DDD", "EQ"), ("EEE", "EQ")])
    store = _store_with_daily(tmp_path, {
        "AAA": (100.0, 1000),      # 100k
        "BBB": (10.0, 500),        # 5k
        "CCC": (50.0, 100000),     # 5M
        "DDD": (200.0, 2000),      # 400k
        "EEE": (999.0, 999999),    # EXCLUDED from the pool cut (position 5)
    })
    spec = _spec(tmp_path, size=3, mcap_pool_size=4)
    report = build_dynamic_universe(store, spec)
    # EEE is outside the top-4 market-cap pool despite its huge liquidity
    assert report.selected == ["CCC", "DDD", "AAA"]
    assert report.candidates == 4


def test_instrument_master_filters_inactive(tmp_path):
    _csv(tmp_path, [("AAA", "EQ"), ("GONE", "EQ"), ("BBB", "EQ")])
    store = _store_with_daily(tmp_path, {"AAA": (10, 10), "BBB": (10, 20),
                                         "GONE": (10, 99)})

    class Master:
        def token_for(self, symbol):
            return None if symbol == "GONE" else "1"

    report = build_dynamic_universe(store, _spec(tmp_path),
                                    instruments=Master())
    assert "GONE" not in report.selected
    assert report.dropped["not_in_instrument_master"] == 1


def test_volume_metric_fallback(tmp_path):
    _csv(tmp_path, [("AAA", "EQ"), ("BBB", "EQ")])
    store = _store_with_daily(tmp_path, {"AAA": (1.0, 500),
                                         "BBB": (1000.0, 100)})
    spec = _spec(tmp_path, size=2, liquidity_metric="volume")
    report = build_dynamic_universe(store, spec)
    assert report.selected == ["AAA", "BBB"]     # by volume, not value


def test_intraday_fallback_when_no_daily_series(tmp_path):
    _csv(tmp_path, [("AAA", "EQ")])
    store = MarketDataStore(tmp_path / "store")
    bars = pd.DataFrame(
        [[pd.Timestamp("2026-07-21 04:00", tz="UTC"), 10, 11, 9, 10.0, 300],
         [pd.Timestamp("2026-07-21 04:15", tz="UTC"), 10, 11, 9, 20.0, 700]],
        columns=list(OHLCV_COLUMNS))
    store.write("AAA", "15m", bars)
    report = build_dynamic_universe(store, _spec(tmp_path, size=1))
    assert report.selected == ["AAA"]


def test_bad_liquidity_metric_is_refused():
    with pytest.raises(ValueError):
        DynamicUniverseSpec.from_dict({"liquidity_metric": "vibes"})


def test_persistence_is_versioned_with_pointer(tmp_path):
    _csv(tmp_path, [("AAA", "EQ"), ("BBB", "EQ")])
    store = _store_with_daily(tmp_path, {"AAA": (10, 100), "BBB": (10, 50)})
    spec = _spec(tmp_path, size=2)
    report = build_dynamic_universe(store, spec)
    persist(report, spec, DAY)
    payload = json.loads(universe_path(spec, DAY).read_text())
    assert payload["symbols"] == ["AAA", "BBB"]
    assert payload["generated_for"] == "2026-07-22"
    lines = pointer_path(spec).read_text().splitlines()
    assert lines[0].startswith("#") and lines[1:] == ["AAA", "BBB"]


def test_load_or_build_is_idempotent_per_day(tmp_path):
    _csv(tmp_path, [("AAA", "EQ"), ("BBB", "EQ")])
    store = _store_with_daily(tmp_path, {"AAA": (10, 100), "BBB": (10, 50)})
    spec = _spec(tmp_path, size=2)
    first = load_or_build(store, spec, day=DAY)
    assert universe_path(spec, DAY).exists()
    # a rebuild the same day READS the file (note says so), even if the
    # store's data changed meanwhile
    store.write("BBB", "1d", pd.DataFrame(
        [[pd.Timestamp("2026-07-21", tz="UTC"), 10, 10, 10, 10, 10 ** 9]],
        columns=list(OHLCV_COLUMNS)))
    second = load_or_build(store, spec, day=DAY)
    assert second.selected == first.selected
    assert any("loaded from" in n for n in second.notes)


def test_daily_refresher_swaps_universe_and_resubscribes(tmp_path):
    _csv(tmp_path, [("AAA", "EQ"), ("BBB", "EQ")])
    store = _store_with_daily(tmp_path, {"AAA": (10, 100), "BBB": (10, 50)})
    spec = _spec(tmp_path, size=2)

    class Clock:
        def now(self):
            return pd.Timestamp("2026-07-22 08:00", tz="Asia/Kolkata")

    class MD:
        def __init__(self):
            self.symbols = ["OLD"]

        def set_symbols(self, symbols):
            self.symbols = list(symbols)

    class Engine:
        def __init__(self):
            self.clock = Clock()
            self.marketdata = MD()
            self.state = type("S", (), {"symbols": ["OLD"],
                                        "universe_report": None})()

    class Source:
        def __init__(self):
            self.resubscribed = None

        def resubscribe(self, symbols):
            self.resubscribed = list(symbols)

    engine, source = Engine(), Source()
    refresher = DailyUniverseRefresher(store, spec)
    symbols = refresher.refresh_if_due(engine, source=source)
    assert symbols == ["AAA", "BBB"]
    assert engine.marketdata.symbols == ["AAA", "BBB"]
    assert source.resubscribed == ["AAA", "BBB"]
    # second call the same day does nothing
    assert refresher.refresh_if_due(engine, source=source) is None
