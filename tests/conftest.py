"""Shared Phase-2 fixtures: calendar, synthetic provider, store, ingestion."""

import hashlib
from pathlib import Path

import pandas as pd
import pytest

from algo.core.calendar import StaticCalendar
from algo.data.ingest import IngestionEngine
from algo.data.providers.synthetic import SyntheticDataProvider
from algo.data.store import MarketDataStore

#: The live dashboard snapshot folder. ``TradingConfig.dashboard_dir`` DEFAULTS
#: here, so any engine built in a test without overriding it writes over a real
#: trading session's snapshots - which is exactly what happened: a suite run
#: replaced the record of a live paper session. Tests must stay out of it.
LIVE_DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard" \
    / "dashboard_data"


@pytest.fixture(autouse=True)
def _never_touch_the_live_dashboard():
    """Fail any test that writes into the real dashboard folder.

    A guard rather than a convention: the default is seductive precisely
    because nothing complains when a test uses it. This catches EVERY writer -
    engine, exporter, or script - regardless of how it was constructed.
    """
    def fingerprint():
        # hash the CONTENT, not just the mtime: filesystem timestamp
        # granularity is coarse enough (~15ms on NTFS) that a fast rewrite can
        # land on the same mtime and slip past an mtime-only check
        if not LIVE_DASHBOARD_DIR.exists():
            return None
        out = {}
        for p in sorted(LIVE_DASHBOARD_DIR.iterdir()):
            if p.is_file():
                out[p.name] = hashlib.sha1(p.read_bytes()).hexdigest()
        return out

    before = fingerprint()
    yield
    after = fingerprint()
    if before != after:
        changed = sorted(set((after or {}).items()) ^ set((before or {}).items()))
        names = sorted({name for name, _ in changed})
        raise AssertionError(
            "this test wrote into the LIVE dashboard folder "
            f"({LIVE_DASHBOARD_DIR}): {names}. Pass "
            "dashboard_dir=str(tmp_path/'dash') in the TradingConfig, or "
            "dashboard_enabled=False.")


@pytest.fixture
def calendar():
    days = pd.bdate_range("2024-01-01", "2024-06-30")
    return StaticCalendar([d.date() for d in days])


@pytest.fixture
def synthetic(calendar):
    return SyntheticDataProvider(seed=7, calendar=calendar)


@pytest.fixture
def store(tmp_path):
    return MarketDataStore(tmp_path / "mkt")


@pytest.fixture
def ingestion(store, synthetic, calendar):
    return IngestionEngine(store, synthetic, calendar)


@pytest.fixture
def symbols():
    return ["AAA", "BBB", "CCC"]
