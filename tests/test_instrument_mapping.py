"""Regression cover for D-038: the symbol -> instrument token mapping.

The first paper session logged "no instrument token for <symbol> - skipping"
for all 99 watchlist symbols while holding open positions in several of them,
and reported ``rows_fetched=0 rows_added=0`` all day without raising a single
error. The instrument master had never been loaded: ``ensure()`` was every
caller's responsibility, five entry points each remembered it separately, and
the production runner did not - in paper mode the one in-engine ``ensure()``
(AngelOneBroker.connect) never runs, because paper uses PaperBroker.

These tests pin the three properties that failure violated:

  1. a lookup LOADS the master itself - no caller can forget;
  2. "master unavailable" and "symbol not listed" are DIFFERENT answers;
  3. an unfetchable symbol is reported as unable-to-fetch, never as an empty
     (= quiet market) result.
"""

import json

import pandas as pd
import pytest

from algo.data.ingest import CANNOT_FETCH, NO_NEW_DATA, IngestionEngine
from algo.data.providers.smartapi import (
    SmartApiDataProvider, SmartApiInstruments,
)
from algo.data.providers.smartapi.instruments import (
    CACHE_FILE, MappingReport, normalize_symbol,
)
from algo.data.store import MarketDataStore

SCRIP = json.dumps([
    {"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE",
     "exch_seg": "NSE", "lotsize": "1", "instrumenttype": ""},
    {"token": "11536", "symbol": "TCS-EQ", "name": "TCS",
     "exch_seg": "NSE", "lotsize": "1", "instrumenttype": ""},
    {"token": "1333", "symbol": "HDFCBANK-EQ", "name": "HDFCBANK",
     "exch_seg": "NSE", "lotsize": "1", "instrumenttype": ""},
])


class CountingDownloader:
    """Records how often the master is actually downloaded."""

    def __init__(self, payload=SCRIP, fail: bool = False):
        self.payload = payload
        self.fail = fail
        self.calls = 0

    def __call__(self, url):
        self.calls += 1
        if self.fail:
            raise OSError("network unreachable")
        return self.payload


def _instruments(**kwargs):
    return SmartApiInstruments("https://x/master.json", **kwargs)


# ===================================================== self-loading lookups

def test_token_resolves_without_any_explicit_ensure():
    """THE regression. A lookup on a freshly constructed instrument map must
    resolve - the paper runner never called ensure() and every symbol silently
    became unmappable."""
    download = CountingDownloader()
    inst = _instruments(downloader=download)
    assert inst.token_for("RELIANCE") == "2885"
    assert inst.tradingsymbol_for("RELIANCE") == "RELIANCE-EQ"
    assert download.calls == 1


def test_lookup_prefers_the_cache_and_never_touches_the_network(tmp_path):
    """A cached master is what a restart normally has (it was sitting on disk,
    unread, during the failed session). Loading it must not need the network."""
    seed = _instruments(cache_dir=tmp_path, downloader=CountingDownloader())
    seed.fetch()
    assert (tmp_path / CACHE_FILE).exists()

    download = CountingDownloader(fail=True)     # any network use would raise
    fresh = _instruments(cache_dir=tmp_path, downloader=download)
    assert fresh.token_for("TCS") == "11536"
    assert download.calls == 0


def test_symbols_list_loads_the_master_too():
    inst = _instruments(downloader=CountingDownloader())
    assert inst.symbols() == ["HDFCBANK", "RELIANCE", "TCS"]


def test_master_is_downloaded_once_across_many_lookups():
    """99 symbols x every cycle must not become 99 downloads."""
    download = CountingDownloader()
    inst = _instruments(downloader=download)
    for _ in range(3):
        for symbol in ("RELIANCE", "TCS", "HDFCBANK", "GHOST"):
            inst.token_for(symbol)
    assert download.calls == 1


def test_a_failed_load_is_not_retried_per_lookup():
    """An unreachable master must be reported, not hammered once per symbol."""
    download = CountingDownloader(fail=True)
    inst = _instruments(downloader=download)
    for symbol in ("RELIANCE", "TCS", "HDFCBANK"):
        assert inst.token_for(symbol) is None
    assert download.calls == 1

    inst.reset()                       # operator retries after fixing the fault
    download.fail = False
    assert inst.token_for("RELIANCE") == "2885"


# ============================================== normalization and aliases

@pytest.mark.parametrize("given, expected", [
    ("reliance", "RELIANCE"),
    ("  RELIANCE  ", "RELIANCE"),
    ("RELIANCE-EQ", "RELIANCE"),       # the broker tradingsymbol round-trips
    ("reliance-eq", "RELIANCE"),
])
def test_symbol_forms_resolve_to_the_same_instrument(given, expected):
    inst = _instruments(downloader=CountingDownloader())
    assert normalize_symbol(given) == expected
    assert inst.token_for(given) == "2885"


def test_other_nse_series_are_not_folded_onto_the_eq_scrip():
    """-BE is a DIFFERENT scrip with a different token; aliasing it onto the
    EQ symbol would silently resolve to an instrument nobody asked for."""
    assert normalize_symbol("RELIANCE-BE") == "RELIANCE-BE"
    inst = _instruments(downloader=CountingDownloader())
    assert inst.token_for("RELIANCE-BE") is None


def test_a_cache_written_by_an_older_build_still_resolves(tmp_path):
    """Symbols were not upper-cased before; a miss caused by that would look
    exactly like a delisting."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"symbol": "reliance", "token": "2885",
                   "tradingsymbol": "RELIANCE-EQ", "name": "RELIANCE",
                   "exchange": "NSE", "lotsize": 1}]
                 ).to_parquet(tmp_path / CACHE_FILE, index=False)
    inst = _instruments(cache_dir=tmp_path, downloader=CountingDownloader(fail=True))
    assert inst.token_for("RELIANCE") == "2885"


# ==================================================== resolve_many reporting

def test_resolve_many_separates_resolved_from_unresolved():
    inst = _instruments(downloader=CountingDownloader())
    report = inst.resolve_many(["RELIANCE", "TCS", "GHOST", "PHANTOM"])
    assert report.total == 4
    assert report.resolved_count == 2
    assert report.unresolved == ["GHOST", "PHANTOM"]
    assert report.master_loaded is True
    assert not report.ok
    assert "2/4" in report.summary_line()
    assert "GHOST" in report.summary_line()


def test_resolve_many_reports_a_clean_watchlist():
    inst = _instruments(downloader=CountingDownloader())
    report = inst.resolve_many(["RELIANCE", "TCS"])
    assert report.ok and report.unresolved == []
    assert "OK" in report.summary_line()


def test_unavailable_master_is_not_reported_as_missing_symbols():
    """The distinction the failed session could not make: every symbol
    unresolved because the MASTER is absent is a different fault from those
    symbols not existing, and the operator must be told which."""
    inst = _instruments(downloader=CountingDownloader(fail=True))
    report = inst.resolve_many(["RELIANCE", "TCS"])
    assert report.master_loaded is False
    assert report.resolved_count == 0
    assert "UNAVAILABLE" in report.summary_line()
    assert "network unreachable" in report.summary_line()
    assert report.to_dict()["master_loaded"] is False


def test_report_examples_are_capped_and_counted():
    inst = _instruments(downloader=CountingDownloader())
    missing = [f"GHOST{i}" for i in range(9)]
    report = inst.resolve_many(missing)
    assert report.unresolved_count == 9
    assert len(report.examples()) == 5
    assert "+4 more" in report.summary_line()


def test_mapping_report_is_json_safe():
    inst = _instruments(downloader=CountingDownloader())
    payload = inst.resolve_many(["RELIANCE", "GHOST"]).to_dict()
    json.dumps(payload)                       # must not raise
    assert payload["resolved"] == 1 and payload["unresolved"] == 1


# ============================== ingestion: unable-to-fetch vs no-new-candles

class _StubSession:
    def ensure(self):
        raise AssertionError("an unmappable symbol must never reach the API")


def _provider_with(instruments):
    return SmartApiDataProvider(_StubSession(), instruments,
                                sleep_fn=lambda s: None)


def test_unmapped_symbol_is_reported_unavailable_not_empty(tmp_path):
    """``empty`` means a quiet market. A symbol with no token is a BROKEN
    request and must never be counted as one."""
    provider = _provider_with(_instruments(downloader=CountingDownloader()))
    engine = IngestionEngine(MarketDataStore(tmp_path), provider)
    report = engine.incremental_update(["GHOST"], "15m")
    result = report.results[0]
    assert result.status == "unavailable"
    assert result.status in CANNOT_FETCH
    assert "no instrument token" in result.detail


def test_unsupported_timeframe_is_also_unavailable(tmp_path):
    """``2h`` is a valid platform timeframe that SmartAPI does not serve - a
    provider limit, not a quiet market. (A timeframe the PLATFORM does not know
    is rejected earlier, by ohlcv.timeframe_minutes.)"""
    provider = _provider_with(_instruments(downloader=CountingDownloader()))
    engine = IngestionEngine(MarketDataStore(tmp_path), provider)
    report = engine.incremental_update(["RELIANCE"], "2h")
    assert report.results[0].status == "unavailable"
    assert "unsupported timeframe" in report.results[0].detail


def test_diagnosis_names_the_reason_and_examples(tmp_path):
    """One operator-readable verdict per cycle, instead of 99 identical
    warnings."""
    provider = _provider_with(_instruments(downloader=CountingDownloader()))
    engine = IngestionEngine(MarketDataStore(tmp_path), provider)
    symbols = [f"GHOST{i}" for i in range(8)]
    diagnosis = engine.incremental_update(symbols, "15m").diagnosis()
    assert diagnosis["ok"] is False
    assert diagnosis["blocked"] == 8 and diagnosis["total"] == 8
    assert "UNABLE TO FETCH" in diagnosis["headline"]
    assert "no instrument token in the NSE master" in diagnosis["headline"]
    assert len(diagnosis["examples"]) == 5
    assert "+3 more" in diagnosis["headline"]


def test_summary_counts_the_two_outcomes_separately(tmp_path):
    provider = _provider_with(_instruments(downloader=CountingDownloader()))
    engine = IngestionEngine(MarketDataStore(tmp_path), provider)
    summary = engine.incremental_update(["GHOST", "PHANTOM"], "15m").summary()
    assert summary["unable_to_fetch"] == 2
    assert summary["no_new_candles"] == 0
    assert summary["rows_fetched"] == 0        # the symptom that was ambiguous


def test_quiet_market_is_reported_as_no_new_candles(tmp_path):
    """The other half of the distinction: a mappable symbol that returns no
    bars is NOT an error, and must not be reported as one."""

    class Quiet:
        name = "quiet"

        def fetch_ohlcv(self, symbol, timeframe, start, end):
            return pd.DataFrame(columns=["date", "open", "high", "low",
                                         "close", "volume"])

        def unavailable_reason(self, symbol, timeframe):
            return None

    report = IngestionEngine(MarketDataStore(tmp_path), Quiet()
                             ).incremental_update(["RELIANCE"], "15m")
    assert report.results[0].status == "empty"
    assert report.results[0].status in NO_NEW_DATA
    diagnosis = report.diagnosis()
    assert diagnosis["ok"] is True
    assert "no new candles" in diagnosis["headline"]


# ================================ one map, shared by every component

def test_broker_and_data_provider_resolve_through_the_same_map():
    """The reported symptom was the trading engine holding positions in symbols
    the market-data provider said it could not identify. They must resolve from
    ONE object, so disagreement is not representable.
    """
    from algo.trading.adapters.angelone import AngelOneBroker
    from algo.trading.config import TradingConfig

    instruments = _instruments(downloader=CountingDownloader())
    provider = _provider_with(instruments)
    broker = AngelOneBroker(TradingConfig.from_dict({"mode": "paper"}),
                            session=object(), instruments=instruments)

    assert broker.instruments is provider.instruments
    for symbol in ("RELIANCE", "TCS", "HDFCBANK"):
        assert broker._token(symbol) == provider.instruments.token_for(symbol)
        assert broker._tradingsymbol(symbol) == \
            provider.instruments.tradingsymbol_for(symbol)


def test_broker_resolves_without_connect_having_primed_the_map():
    """``AngelOneBroker.connect`` used to be the only in-engine ensure(). A
    lookup must not depend on it having run."""
    from algo.trading.adapters.angelone import AngelOneBroker
    from algo.trading.config import TradingConfig

    broker = AngelOneBroker(TradingConfig.from_dict({"mode": "paper"}),
                            session=object(),
                            instruments=_instruments(
                                downloader=CountingDownloader()))
    assert broker._token("RELIANCE") == "2885"       # no connect() called


def test_provider_lists_symbols_without_priming():
    provider = _provider_with(_instruments(downloader=CountingDownloader()))
    assert provider.list_symbols() == ["HDFCBANK", "RELIANCE", "TCS"]


def test_provider_without_the_hook_still_works(tmp_path):
    """``unavailable_reason`` is optional - a provider predating it (or a test
    double) must be unaffected."""

    class Legacy:
        name = "legacy"

        def fetch_ohlcv(self, symbol, timeframe, start, end):
            return pd.DataFrame(columns=["date", "open", "high", "low",
                                         "close", "volume"])

    report = IngestionEngine(MarketDataStore(tmp_path), Legacy()
                             ).incremental_update(["RELIANCE"], "15m")
    assert report.results[0].status == "empty"
