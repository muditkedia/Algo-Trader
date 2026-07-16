"""Kotak Neo provider - fully mocked (no SDK install, no real credentials).

A FakeNeoClient stands in for neo_api_client.NeoAPI and is injected via the
session's client_factory / the instruments downloader, so auth, instrument
loading, OHLC mapping, incremental ingestion, scheduling, and scanning are all
exercised without network or secrets.
"""

import pandas as pd
import pytest

from algo.data.ingest import IngestionEngine
from algo.data.providers.kotak import (
    KotakAuthError, KotakNeoConfig, KotakNeoDataProvider, KotakNeoInstruments,
    KotakNeoSession,
)
from algo.data.providers.kotak.config import KotakConfigError
from algo.schedule.jobs import DataJobs


class FakeNeoClient:
    """Records calls; returns representative Kotak-shaped responses."""

    def __init__(self, ohlc=None, scrip=None, login_ok=True):
        self.calls = []
        self._ohlc = ohlc or {"open": 100.0, "high": 105.0, "low": 99.0,
                              "close": 103.0, "volume": 12345.0}
        self._scrip = scrip
        self._login_ok = login_ok

    def totp_login(self, mobile_number, ucc, totp):
        self.calls.append(("totp_login", mobile_number, ucc, totp))
        return ({"data": {"token": "view", "sid": "s"}} if self._login_ok
                else {"error": [{"message": "invalid totp"}]})

    def totp_validate(self, mpin):
        self.calls.append(("totp_validate", mpin))
        return {"data": {"token": "trade", "sid": "s"}}

    def scrip_master(self, exchange_segment=None):
        self.calls.append(("scrip_master", exchange_segment))
        return self._scrip

    def quotes(self, instrument_tokens=None, quote_type=None):
        self.calls.append(("quotes", instrument_tokens, quote_type))
        return {"data": [dict(self._ohlc)]}

    def logout(self):
        self.calls.append(("logout",))


_SCRIP = [
    {"pSymbol": "11536", "pTrdSymbol": "TCS", "pExchSeg": "nse_cm",
     "pSymbolName": "Tata Consultancy"},
    {"pSymbol": "2885", "pTrdSymbol": "RELIANCE", "pExchSeg": "nse_cm",
     "pSymbolName": "Reliance Industries"},
]


def _logged_in_session(client):
    cfg = KotakNeoConfig(consumer_key="ck", mobile_number="+9199", ucc="U1",
                         mpin="1234", totp="000000")
    return KotakNeoSession(cfg, client_factory=lambda c: client).login()


# ------------------------------------------------------------------- config

def test_config_from_env_and_masking():
    env = {"KOTAK_NEO_CONSUMER_KEY": "ck", "KOTAK_NEO_MOBILE": "+9199",
           "KOTAK_NEO_UCC": "U1", "KOTAK_NEO_MPIN": "1234",
           "KOTAK_NEO_TOTP": "000000"}
    cfg = KotakNeoConfig.from_env(env)
    cfg.require_login()  # complete -> no raise
    assert "ck" not in repr(cfg) and "1234" not in repr(cfg)


def test_config_missing_raises():
    cfg = KotakNeoConfig.from_env({"KOTAK_NEO_CONSUMER_KEY": "ck"})
    with pytest.raises(KotakConfigError):
        cfg.require_login()


# -------------------------------------------------------------------- auth

def test_session_login_flow_uses_env_creds():
    fake = FakeNeoClient()
    session = _logged_in_session(fake)
    assert session.logged_in and session.client is fake
    assert ("totp_login", "+9199", "U1", "000000") in fake.calls
    assert ("totp_validate", "1234") in fake.calls


def test_session_client_before_login_raises():
    cfg = KotakNeoConfig(consumer_key="ck", mobile_number="+9199", ucc="U1",
                         mpin="1234", totp="000000")
    session = KotakNeoSession(cfg, client_factory=lambda c: FakeNeoClient())
    with pytest.raises(KotakAuthError):
        _ = session.client


def test_session_login_failure_raises():
    fake = FakeNeoClient(login_ok=False)
    cfg = KotakNeoConfig(consumer_key="ck", mobile_number="+9199", ucc="U1",
                         mpin="1234", totp="000000")
    with pytest.raises(KotakAuthError):
        KotakNeoSession(cfg, client_factory=lambda c: fake).login()


# ------------------------------------------------------------- instruments

def test_instruments_from_records():
    session = _logged_in_session(FakeNeoClient(scrip=_SCRIP))
    instr = KotakNeoInstruments(session)
    frame = instr.fetch("nse_cm")
    assert list(frame.columns) == ["symbol", "instrument_token",
                                   "exchange_segment", "name"]
    assert instr.token_for("RELIANCE", "nse_cm") == "2885"
    assert instr.symbols("nse_cm") == ["RELIANCE", "TCS"]


def test_instruments_from_filespaths(tmp_path):
    csv = "pSymbol,pTrdSymbol,pExchSeg,pSymbolName\n2885,RELIANCE,nse_cm,Reliance\n"
    scrip = {"filesPaths": ["https://x/prod/2025/transformed/nse_cm.csv"]}
    session = _logged_in_session(FakeNeoClient(scrip=scrip))
    instr = KotakNeoInstruments(session, cache_dir=tmp_path / "inst",
                                downloader=lambda url: csv)
    frame = instr.fetch("nse_cm")
    assert instr.token_for("RELIANCE") == "2885"
    assert (tmp_path / "inst" / "kotak_nse_cm.parquet").exists()  # cached


# ---------------------------------------------------------------- provider

def _provider(client, calendar, now="2024-03-15 16:00"):
    session = _logged_in_session(client)
    instr = KotakNeoInstruments(session)
    instr.fetch("nse_cm")
    return KotakNeoDataProvider(
        session, instr, exchange_segment="nse_cm", calendar=calendar,
        now_fn=lambda: pd.Timestamp(now, tz="UTC")), instr


def test_provider_returns_today_bar(calendar):
    prov, _ = _provider(FakeNeoClient(scrip=_SCRIP), calendar)
    df = prov.fetch_ohlcv("RELIANCE", "1d", "2024-03-01", "2024-03-15")
    assert len(df) == 1
    assert df["close"].iloc[0] == 103.0
    assert df["date"].iloc[0] == pd.Timestamp("2024-03-15", tz="UTC")


def test_provider_nested_ohlc_shape(calendar):
    client = FakeNeoClient(scrip=_SCRIP)
    client._ohlc = {"ohlc": {"open": 10, "high": 12, "low": 9, "close": 11},
                    "volume": 500}
    prov, _ = _provider(client, calendar)
    df = prov.fetch_ohlcv("TCS", "1d", "2024-03-01", "2024-03-15")
    assert df["high"].iloc[0] == 12.0 and df["volume"].iloc[0] == 500.0


def test_provider_backfill_window_is_empty(calendar):
    prov, _ = _provider(FakeNeoClient(scrip=_SCRIP), calendar)
    # request a purely historical window (before today's only available bar)
    df = prov.fetch_ohlcv("RELIANCE", "1d", "2024-01-01", "2024-01-31")
    assert df.empty


def test_provider_unknown_symbol_and_intraday_empty(calendar):
    prov, _ = _provider(FakeNeoClient(scrip=_SCRIP), calendar)
    assert prov.fetch_ohlcv("NOTLISTED", "1d", "2024-03-01", "2024-03-15").empty
    assert prov.fetch_ohlcv("RELIANCE", "5m", "2024-03-15", "2024-03-15").empty


# --------------------------------------------------- scheduler + scanner

def test_incremental_ingest_and_idempotent(store, calendar):
    prov, _ = _provider(FakeNeoClient(scrip=_SCRIP), calendar)
    engine = IngestionEngine(store, prov, calendar)
    r1 = engine.incremental_update(["RELIANCE"], "1d", end="2024-03-15",
                                   start_if_empty="2024-03-01")
    assert r1.results[0].status == "ok"
    assert store.coverage("RELIANCE", "1d")["rows"] == 1
    # re-run same day -> no new bar (idempotent)
    r2 = engine.incremental_update(["RELIANCE"], "1d", end="2024-03-15")
    assert r2.results[0].status in ("up_to_date", "empty")
    assert store.coverage("RELIANCE", "1d")["rows"] == 1


def test_scheduler_and_scanner_integration(store, calendar):
    prov, instr = _provider(FakeNeoClient(scrip=_SCRIP), calendar)
    jobs = DataJobs(IngestionEngine(store, prov, calendar), calendar)
    report = jobs.daily_update(["RELIANCE", "TCS"], "1d", as_of="2024-03-15",
                               start_if_empty="2024-03-01")
    assert all(r.status == "ok" for r in report.results)

    from algo.scanner.engine import ScanEngine
    from algo.strategies.base import StrategyMeta, StrategyProfile
    from algo.core.enums import Direction, HoldingScope

    class Fire(StrategyProfile):
        meta = StrategyMeta(name="f", version="1", direction=Direction.LONG,
                            holding_scope=HoldingScope.INTRADAY,
                            required_columns=("close",), enabled=True)

        def entry_signal(self, df):
            return self.no_signal(df) if self.missing_columns(df) \
                else pd.Series(True, index=df.index)

    result = ScanEngine(store, [Fire()], timeframe="1d").scan(
        "2024-03-15", instr.symbols("nse_cm"))
    assert result.n_symbols_with_data == 2
    assert len(result.opportunities) == 2
