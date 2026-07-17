"""Paper engine: measurement gating, open/manage/close, square-off, resume."""

import numpy as np
import pandas as pd
import pytest

from algo.core.enums import Direction, HoldingScope
from algo.evidence.database import EvidenceDB, MEMORY
from algo.evidence.logger import EvidenceLogger
from algo.paper.engine import PaperEngine
from algo.strategies.base import StrategyMeta, StrategyProfile


class AlwaysLong(StrategyProfile):
    meta = StrategyMeta(name="paper_test", version="1.0",
                        direction=Direction.LONG,
                        holding_scope=HoldingScope.INTRADAY, timeframe="15m",
                        min_bars=3, required_columns=("close",), enabled=True)

    def entry_signal(self, df):
        return pd.Series(True, index=df.index)


def _bars(day: str, n: int, closes=None, lows=None):
    dates = pd.date_range(f"{day} 09:15", periods=n, freq="15min",
                          tz="Asia/Kolkata").tz_convert("UTC")
    closes = np.asarray(closes if closes is not None
                        else np.linspace(100, 101, n))
    return pd.DataFrame({
        "date": dates, "open": closes, "high": closes + 0.5,
        "low": np.asarray(lows) if lows is not None else closes - 0.5,
        "close": closes, "volume": np.full(n, 1000.0)})


@pytest.fixture
def env(store):
    db = EvidenceDB(MEMORY)
    log = EvidenceLogger(db)
    log.upsert_instrument("AAA")
    yield store, db, log
    db.close()


def _measured(log, name="paper_test", version="1.0"):
    sid = log.register_strategy(name, version)
    log.set_strategy_status(sid, "measured", "test PASS")
    return sid


def test_gating_refuses_unmeasured(env, tmp_path):
    store, db, log = env
    with pytest.raises(RuntimeError) as excinfo:
        PaperEngine(store, [AlwaysLong()], log,
                    state_path=tmp_path / "p.json")
    assert "survived measurement" in str(excinfo.value)


def test_gating_admits_measured_and_allow_unmeasured_flag(env, tmp_path):
    store, db, log = env
    _measured(log)
    engine = PaperEngine(store, [AlwaysLong()], log,
                         state_path=tmp_path / "p.json")
    assert [s.name for s in engine.strategies] == ["paper_test"]
    # unmeasured admitted only with the explicit override
    log2 = EvidenceLogger(EvidenceDB(MEMORY))
    engine2 = PaperEngine(store, [AlwaysLong()], log2, allow_unmeasured=True,
                          state_path=tmp_path / "p2.json")
    assert engine2.strategies


def test_open_manage_close_stop_loss(env, tmp_path):
    store, db, log = env
    _measured(log)
    store.write("AAA", "15m", _bars("2024-03-04", 6))
    engine = PaperEngine(store, [AlwaysLong()], log, stake_per_trade=50_000,
                         state_path=tmp_path / "p.json")

    opened = engine.cycle(as_of="2024-03-04 05:00+00:00", symbols=["AAA"])
    assert opened["opened"] == ["AAA"]
    position = engine.positions["AAA"]
    assert position.stop_price < position.entry_price
    # the recorded paper signal was upgraded to executed
    row = db.connection.execute(
        "SELECT disposition FROM signals WHERE symbol='AAA'").fetchone()
    assert row["disposition"] == "executed"

    # next bars crash through the stop -> position closes as stop_loss
    crash = _bars("2024-03-04", 10,
                  closes=list(np.linspace(100, 101, 6)) + [95, 94, 94, 94],
                  lows=list(np.linspace(99.5, 100.5, 6)) + [92, 92, 92, 92])
    store.write("AAA", "15m", crash)
    result = engine.cycle(as_of="2024-03-04 07:00+00:00", symbols=["AAA"])
    assert any(c.startswith("AAA:stop_loss") for c in result["closed"])
    trade = db.connection.execute(
        "SELECT * FROM trades WHERE mode='paper'").fetchone()
    assert trade["exit_reason"] == "stop_loss"
    assert trade["profit_ratio"] < 0                 # loss net of costs


def test_session_squareoff_closes_intraday(env, tmp_path):
    store, db, log = env
    _measured(log)
    store.write("AAA", "15m", _bars("2024-03-04", 6))
    engine = PaperEngine(store, [AlwaysLong()], log,
                         state_path=tmp_path / "p.json")
    engine.cycle(as_of="2024-03-04 05:00+00:00", symbols=["AAA"])
    assert "AAA" in engine.positions
    # full session including bars past the 15:15 IST cutoff
    store.write("AAA", "15m", _bars("2024-03-04", 25))
    result = engine.cycle(as_of="2024-03-04 11:00+00:00", symbols=["AAA"])
    assert any("session_squareoff" in c for c in result["closed"])


def test_state_resumes_across_restart(env, tmp_path):
    store, db, log = env
    _measured(log)
    store.write("AAA", "15m", _bars("2024-03-04", 6))
    state = tmp_path / "p.json"
    engine = PaperEngine(store, [AlwaysLong()], log, state_path=state)
    engine.cycle(as_of="2024-03-04 05:00+00:00", symbols=["AAA"])
    assert state.exists()

    resumed = PaperEngine(store, [AlwaysLong()], log, state_path=state)
    assert "AAA" in resumed.positions
    assert resumed.positions["AAA"].entry_price == \
        engine.positions["AAA"].entry_price


def test_capacity_and_confidence_floor(env, tmp_path):
    store, db, log = env
    _measured(log)
    for sym in ("AAA", "BBB", "CCC"):
        log.upsert_instrument(sym)
        store.write(sym, "15m", _bars("2024-03-04", 6))
    engine = PaperEngine(store, [AlwaysLong()], log, max_positions=2,
                         state_path=tmp_path / "p.json")
    result = engine.cycle(as_of="2024-03-04 05:00+00:00",
                          symbols=["AAA", "BBB", "CCC"])
    assert len(result["opened"]) == 2                # capacity respected

    picky = PaperEngine(store, [AlwaysLong()], log, min_confidence=0.99,
                        state_path=tmp_path / "p2.json")
    result = picky.cycle(as_of="2024-03-04 05:00+00:00", symbols=["CCC"])
    assert result["opened"] == []                    # floor respected
