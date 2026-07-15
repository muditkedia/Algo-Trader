"""Universe management + configurable filtering correctness."""

import pandas as pd
import pytest

from algo.evidence.database import EvidenceDB, MEMORY
from algo.evidence.logger import EvidenceLogger
from algo.universe.filters import build_filters
from algo.universe.manager import UniverseManager


def _flat(close, volume, n=25):
    dates = pd.bdate_range("2024-01-01", periods=n, tz="UTC")
    return pd.DataFrame({
        "date": dates, "open": close, "high": close * 1.01,
        "low": close * 0.99, "close": close, "volume": volume})


@pytest.fixture
def populated(store):
    store.write("AAA", "1d", _flat(100.0, 1_000_000))   # tv = 1e8
    store.write("BBB", "1d", _flat(10.0, 100.0))         # cheap + illiquid
    store.write("CCC", "1d", _flat(5000.0, 5000.0))      # expensive
    return store


def test_metrics_frame_values(populated):
    mgr = UniverseManager(populated)
    frame = mgr.metrics_frame(["AAA", "BBB", "CCC"], lookback=20)
    assert frame.loc["AAA", "price"] == pytest.approx(100.0)
    assert frame.loc["AAA", "avg_volume"] == pytest.approx(1_000_000)
    assert frame.loc["AAA", "avg_traded_value"] == pytest.approx(1e8)
    assert "sector" in frame.columns and "market_cap" in frame.columns


def test_price_and_liquidity_filtering(populated):
    mgr = UniverseManager(populated)
    filters = build_filters({"min_price": 20, "max_price": 4000,
                             "min_avg_traded_value": 1e6})
    result = mgr.eligible(["AAA", "BBB", "CCC"], filters, lookback=20)
    assert result.kept == ["AAA"]
    assert result.dropped["BBB"] == "price_band"     # 10 < 20
    assert result.dropped["CCC"] == "price_band"     # 5000 > 4000


def test_sector_and_blacklist_filtering(populated):
    mgr = UniverseManager(populated)
    attrs = pd.DataFrame({"sector": ["IT", "Energy", "IT"]},
                         index=["AAA", "BBB", "CCC"])
    filters = build_filters({"sectors": ["IT"], "blacklist": ["CCC"]})
    result = mgr.eligible(["AAA", "BBB", "CCC"], filters, attributes=attrs)
    assert result.kept == ["AAA"]                    # BBB not IT, CCC blacklisted
    assert result.dropped["CCC"] == "blacklist"
    assert result.dropped["BBB"] == "sector"


def test_symbol_master_register_and_load(populated):
    db = EvidenceDB(MEMORY)
    mgr = UniverseManager(populated, evidence_logger=EvidenceLogger(db))
    mgr.register_symbols([{"symbol": "AAA", "sector": "IT"},
                          {"symbol": "BBB", "sector": "Energy"}])
    attrs = mgr.load_attributes()
    assert attrs.loc["AAA", "sector"] == "IT"
    # metrics_frame auto-joins the registered attributes
    frame = mgr.metrics_frame(["AAA", "BBB"], lookback=20)
    assert frame.loc["AAA", "sector"] == "IT"
    db.close()
