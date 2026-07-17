"""SmartAPI provider - fully mocked (no SDK install, no credentials, no net).

A FakeSmartConnect stands in for SmartApi.SmartConnect via the session's
client_factory; the instrument downloader is injected. Covers Task 4: imports,
configuration loading (.env), provider wiring, download pipeline (chunking,
throttling, parsing), and error handling - everything except the real
authenticated request.
"""

import json

import numpy as np
import pandas as pd
import pytest

from algo.core.config import load_env_file
from algo.data.ingest import IngestionEngine
from algo.data.providers.smartapi import (
    SmartApiConfig, SmartApiConfigError, SmartApiDataProvider,
    SmartApiInstruments, SmartApiSession, SmartApiAuthError,
)

ENV = {"SMARTAPI_API_KEY": "k", "SMARTAPI_CLIENT_CODE": "C1",
       "SMARTAPI_PIN": "0000", "SMARTAPI_TOTP": "123456"}

SCRIP = json.dumps([
    {"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE",
     "exch_seg": "NSE", "lotsize": "1", "instrumenttype": ""},
    {"token": "11536", "symbol": "TCS-EQ", "name": "TCS",
     "exch_seg": "NSE", "lotsize": "1", "instrumenttype": ""},
    {"token": "99999", "symbol": "NIFTY24FUT", "name": "NIFTY",
     "exch_seg": "NFO", "lotsize": "50", "instrumenttype": "FUTIDX"},
    {"token": "500325", "symbol": "RELIANCE", "name": "RELIANCE",
     "exch_seg": "BSE", "lotsize": "1", "instrumenttype": ""},
])


def _candles(start, periods, freq="1D"):
    """SmartAPI-shaped candle rows with IST offsets."""
    stamps = pd.date_range(start, periods=periods, freq=freq,
                           tz="Asia/Kolkata")
    return [[ts.isoformat(), 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i,
             1000 + i] for i, ts in enumerate(stamps)]


class FakeSmartConnect:
    def __init__(self, candles=None, fail_first_candles=False):
        self.calls = []
        self._candles = candles if candles is not None else _candles(
            "2024-01-01 09:15", 5)
        self._fail_next = fail_first_candles
        self.session_generations = 0

    def generateSession(self, client_code, pin, totp):
        self.calls.append(("generateSession", client_code))
        self.session_generations += 1
        return {"status": True, "data": {"jwtToken": "j", "refreshToken": "r",
                                         "feedToken": "f"}}

    def generateToken(self, refresh_token):
        self.calls.append(("generateToken",))
        return {"status": True, "data": {"jwtToken": "j2",
                                         "refreshToken": "r2"}}

    def getProfile(self, refresh_token):
        self.calls.append(("getProfile",))
        return {"status": True, "data": {"name": "Test User",
                                         "exchanges": ["NSE"]}}

    def terminateSession(self, client_code):
        self.calls.append(("terminateSession", client_code))
        return {"status": True, "data": {}}

    def getCandleData(self, params):
        self.calls.append(("getCandleData", dict(params)))
        if self._fail_next:
            self._fail_next = False
            return {"status": False, "message": "Invalid Token",
                    "errorcode": "AG8001"}
        return {"status": True, "data": list(self._candles)}

    def ltpData(self, exchange, tradingsymbol, symboltoken):
        self.calls.append(("ltpData", exchange, tradingsymbol, symboltoken))
        return {"status": True, "data": {"tradingsymbol": tradingsymbol,
                                         "ltp": 2900.5}}


def _session(client):
    config = SmartApiConfig.from_env(ENV)
    return SmartApiSession(config, client_factory=lambda c: client)


def _provider(client, **kwargs):
    session = _session(client)
    instruments = SmartApiInstruments("https://x/master.json",
                                      downloader=lambda url: SCRIP)
    instruments.fetch()
    return SmartApiDataProvider(session, instruments,
                                sleep_fn=lambda s: None, **kwargs)


# --------------------------------------------------------------- config/.env

def test_env_file_loading(tmp_path, monkeypatch):
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nSMARTAPI_API_KEY=abc\nexport SMARTAPI_PIN='1234'\n\n"
        "SMARTAPI_CLIENT_CODE=\"C9\"\n", encoding="utf-8")
    loaded = load_env_file(env_file)
    assert loaded == {"SMARTAPI_API_KEY": "abc", "SMARTAPI_PIN": "1234",
                      "SMARTAPI_CLIENT_CODE": "C9"}
    config = SmartApiConfig.from_env(env_file=None)
    assert config.api_key == "abc" and config.pin == "1234"
    assert "abc" not in repr(config) and "1234" not in repr(config)


def test_missing_credentials_reported_by_name():
    config = SmartApiConfig.from_env({})
    missing = config.missing()
    assert "SMARTAPI_API_KEY" in missing
    assert "SMARTAPI_TOTP_SECRET (or SMARTAPI_TOTP)" in missing
    with pytest.raises(SmartApiConfigError) as excinfo:
        config.require_login()
    assert ".env.example" in str(excinfo.value)


# --------------------------------------------------------------------- auth

def test_login_refresh_profile_logout_flow():
    client = FakeSmartConnect()
    session = _session(client).login()
    assert session.logged_in and session.refresh_token == "r"
    session.refresh()
    assert session.refresh_token == "r2"
    assert session.profile()["name"] == "Test User"
    session.logout()
    assert not session.logged_in
    assert ("terminateSession", "C1") in client.calls


def test_login_failure_raises_with_message_not_secrets():
    class Failing(FakeSmartConnect):
        def generateSession(self, client_code, pin, totp):
            return {"status": False, "message": "Invalid totp"}
    with pytest.raises(SmartApiAuthError) as excinfo:
        _session(Failing()).login()
    assert "Invalid totp" in str(excinfo.value)
    assert "0000" not in str(excinfo.value)      # PIN never leaks


def test_client_before_login_raises():
    with pytest.raises(SmartApiAuthError):
        _ = _session(FakeSmartConnect()).client


# -------------------------------------------------------------- instruments

def test_instrument_master_filters_nse_equities(tmp_path):
    instruments = SmartApiInstruments("https://x/m.json", cache_dir=tmp_path,
                                      downloader=lambda url: SCRIP)
    frame = instruments.fetch()
    assert sorted(frame["symbol"]) == ["RELIANCE", "TCS"]   # NSE -EQ only
    assert instruments.token_for("RELIANCE") == "2885"
    assert instruments.tradingsymbol_for("RELIANCE") == "RELIANCE-EQ"
    assert instruments.token_for("NIFTY24FUT") is None      # NFO excluded
    # cache round-trip
    fresh = SmartApiInstruments("https://x/m.json", cache_dir=tmp_path,
                                downloader=lambda url: 1 / 0)
    assert fresh.ensure().equals(frame)                     # no re-download


# ----------------------------------------------------------------- provider

def test_candles_parsed_to_canonical_utc():
    provider = _provider(FakeSmartConnect())
    frame = provider.fetch_ohlcv("RELIANCE", "1d", "2024-01-01", "2024-01-10")
    assert len(frame) == 5
    assert list(frame.columns) == ["date", "open", "high", "low", "close",
                                   "volume"]
    assert str(frame["date"].dtype).endswith("UTC]")   # tz-aware UTC
    # 09:15 IST == 03:45 UTC
    assert frame["date"].iloc[0].hour == 3 and frame["date"].iloc[0].minute == 45


def test_long_range_is_chunked_per_documented_limits():
    client = FakeSmartConnect(candles=[])
    provider = _provider(client, max_days_per_request={"1d": 100})
    provider.fetch_ohlcv("TCS", "1d", "2023-01-01", "2023-12-31")   # 365 days
    requests = [c for c in client.calls if c[0] == "getCandleData"]
    assert len(requests) == 4                       # ceil(365/100)
    assert all(r[1]["interval"] == "ONE_DAY" for r in requests)
    assert all(r[1]["symboltoken"] == "11536" for r in requests)


def test_throttle_enforced_between_requests():
    sleeps = []
    client = FakeSmartConnect(candles=[])
    session = _session(client)
    instruments = SmartApiInstruments("https://x/m.json",
                                      downloader=lambda url: SCRIP)
    instruments.fetch()
    provider = SmartApiDataProvider(session, instruments,
                                    min_request_interval_s=0.4,
                                    max_days_per_request={"1d": 50},
                                    sleep_fn=sleeps.append)
    provider.fetch_ohlcv("TCS", "1d", "2023-01-01", "2023-06-30")
    assert len(sleeps) >= 2                          # throttled between chunks
    assert all(0 < s <= 0.4 for s in sleeps)


def test_rate_limit_is_retried_with_backoff_not_treated_as_bad_data():
    """The API rejects bulk downloads with a NON-JSON body, which the SDK
    raises as a parse error. It must back off and retry, not fail the symbol."""
    class RateLimited(FakeSmartConnect):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def getCandleData(self, params):
            self.attempts += 1
            if self.attempts < 3:
                raise Exception("Couldn't parse the JSON response received "
                                "from the server: b'Access denied because of "
                                "exceeding access rate'")
            return super().getCandleData(params)

    client = RateLimited()
    sleeps = []
    session = _session(client)
    instruments = SmartApiInstruments("https://x/m.json",
                                      downloader=lambda url: SCRIP)
    instruments.fetch()
    provider = SmartApiDataProvider(session, instruments,
                                    sleep_fn=sleeps.append,
                                    min_request_interval_s=0.0,  # isolate backoff
                                    rate_limit_backoff_s=1.0)
    frame = provider.fetch_ohlcv("RELIANCE", "1d", "2024-01-01", "2024-01-10")
    assert len(frame) == 5                 # succeeded after backing off
    assert client.attempts == 3
    backoffs = [s for s in sleeps if s >= 1.0]
    assert backoffs == [1.0, 2.0]          # exponential


def test_rate_limit_gives_up_after_configured_retries():
    class AlwaysLimited(FakeSmartConnect):
        def getCandleData(self, params):
            raise Exception("Access denied because of exceeding access rate")

    session = _session(AlwaysLimited())
    instruments = SmartApiInstruments("https://x/m.json",
                                      downloader=lambda url: SCRIP)
    instruments.fetch()
    provider = SmartApiDataProvider(session, instruments,
                                    sleep_fn=lambda s: None,
                                    rate_limit_retries=2)
    with pytest.raises(Exception, match="(?i)access rate"):
        provider.fetch_ohlcv("RELIANCE", "1d", "2024-01-01", "2024-01-10")


def test_token_error_triggers_one_refresh_retry():
    client = FakeSmartConnect(fail_first_candles=True)
    provider = _provider(client)
    frame = provider.fetch_ohlcv("RELIANCE", "1d", "2024-01-01", "2024-01-10")
    assert len(frame) == 5                           # retry succeeded
    assert ("generateToken",) in client.calls        # session was refreshed


def test_unknown_symbol_and_timeframe_are_graceful():
    provider = _provider(FakeSmartConnect())
    assert provider.fetch_ohlcv("GHOST", "1d", "2024-01-01", "2024-01-05").empty
    assert provider.fetch_ohlcv("RELIANCE", "2m", "2024-01-01",
                                "2024-01-05").empty


def test_latest_quote():
    provider = _provider(FakeSmartConnect())
    quote = provider.latest_quote("RELIANCE")
    assert quote["ltp"] == 2900.5
    assert provider.latest_quote("GHOST") is None


# ------------------------------------------------------------ pipeline wiring

def test_ingestion_pipeline_with_smartapi(store):
    candles = _candles("2024-01-01 09:15", 30)
    provider = _provider(FakeSmartConnect(candles=candles))
    engine = IngestionEngine(store, provider)
    report = engine.incremental_update(["RELIANCE"], "1d",
                                       end="2024-02-15",
                                       start_if_empty="2024-01-01")
    assert report.results[0].status == "ok"
    assert store.coverage("RELIANCE", "1d")["rows"] == 30
    # duplicate protection: same window again -> nothing new admitted
    again = engine.incremental_update(["RELIANCE"], "1d", end="2024-02-15")
    assert store.coverage("RELIANCE", "1d")["rows"] == 30
