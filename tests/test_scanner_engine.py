"""Scanner engine: processes every eligible stock, ranks, records evidence."""

import pandas as pd
import pytest

from algo.core.enums import Direction, HoldingScope
from algo.evidence.database import EvidenceDB, MEMORY
from algo.evidence.logger import EvidenceLogger
from algo.scanner.engine import ScanEngine
from algo.strategies.base import StrategyMeta, StrategyProfile


class AlwaysFire(StrategyProfile):
    meta = StrategyMeta(name="always", version="1.0", direction=Direction.LONG,
                        holding_scope=HoldingScope.INTRADAY,
                        required_columns=("close",), enabled=True)

    def entry_signal(self, dataframe):
        if self.missing_columns(dataframe):
            return self.no_signal(dataframe)
        return pd.Series(True, index=dataframe.index)


class NeedsMissingCol(StrategyProfile):
    meta = StrategyMeta(name="needs", version="1.0", direction=Direction.LONG,
                        holding_scope=HoldingScope.INTRADAY,
                        required_columns=("ema_200",), enabled=True)

    def entry_signal(self, dataframe):
        if self.missing_columns(dataframe):
            return self.no_signal(dataframe)
        return pd.Series(True, index=dataframe.index)


@pytest.fixture
def loaded(store, synthetic, symbols):
    for sym in symbols:
        store.write(sym, "1d", synthetic.fetch_ohlcv(
            sym, "1d", "2024-01-01", "2024-03-31"))
    return store, symbols


def test_scan_produces_ranked_opportunities(loaded):
    store, symbols = loaded
    engine = ScanEngine(store, [AlwaysFire()], timeframe="1d")
    result = engine.scan("2024-03-31", symbols)
    assert result.n_symbols_with_data == len(symbols)
    assert result.n_candidates == len(symbols)      # every symbol fires once
    assert len(result.opportunities) == len(symbols)
    assert [o.rank for o in result.opportunities] == [1, 2, 3]
    assert result.duration_ms >= 0


def test_scan_with_no_strategies_still_walks_universe(loaded):
    store, symbols = loaded
    engine = ScanEngine(store, [], timeframe="1d")
    result = engine.scan("2024-03-31", symbols)
    assert result.n_symbols_with_data == len(symbols)  # processed every stock
    assert result.opportunities == []                  # nothing to fire


def test_missing_indicator_column_yields_no_candidate(loaded):
    store, symbols = loaded
    engine = ScanEngine(store, [NeedsMissingCol()], timeframe="1d")
    result = engine.scan("2024-03-31", symbols)
    assert result.n_candidates == 0  # guard returns no_signal, never raises


def test_scan_records_every_candidate_to_evidence(loaded):
    store, symbols = loaded
    db = EvidenceDB(MEMORY)
    log = EvidenceLogger(db)
    for sym in symbols:
        log.upsert_instrument(sym)          # FK target
    engine = ScanEngine(store, [AlwaysFire()], timeframe="1d",
                        evidence_logger=log)
    result = engine.scan("2024-03-31", symbols)
    n = db.connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    assert n == result.n_candidates == len(symbols)
    row = db.connection.execute(
        "SELECT disposition FROM signals LIMIT 1").fetchone()
    assert row["disposition"] == "recorded_only"
    db.close()


def test_scan_performance_scales(store, synthetic):
    many = [f"SYM{i:03d}" for i in range(60)]
    for sym in many:
        store.write(sym, "1d", synthetic.fetch_ohlcv(
            sym, "1d", "2024-01-01", "2024-02-01"))
    engine = ScanEngine(store, [AlwaysFire()], timeframe="1d")
    result = engine.scan("2024-02-01", many)
    assert result.n_symbols_with_data == 60
    assert len(result.opportunities) == 60
    assert result.duration_ms < 10_000  # 60 symbols scan well under 10s
