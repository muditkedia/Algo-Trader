"""Dashboard/engine consistency (§11): one runtime state, no parallel copies.

The dashboard must display exactly what the engine works with. Two failure
modes are guarded here:

  * the same quantity computed twice, in two places, drifting apart;
  * the same NAME meaning two different things in two panels.

Both existed. ``open_risk`` was recomputed in the exporter with the same
formula the risk engine already owns, and ``portfolio_value`` meant the day's
static allowance in portfolio.json while meaning the live mark in live.json.
"""

import json

import pandas as pd
import pytest

from algo.data.store import MarketDataStore
from algo.trading.config import TradingConfig
from algo.trading.engine import ProductionEngine
from algo.marketdata import MarketState
from algo.trading.models import Position
from algo.trading.risk import position_open_risk


def _history(n=30, price=100.0):
    opens = pd.date_range("2026-07-20 09:15", periods=n, freq="15min",
                          tz="Asia/Kolkata")
    return pd.DataFrame({"date": opens, "open": price, "high": price + 1,
                         "low": price - 1, "close": price, "volume": 1000})


@pytest.fixture
def engine(tmp_path):
    store = MarketDataStore(tmp_path / "store")
    for sym in ("RELIANCE", "TCS"):
        store.write(sym, "15m", _history())
    (tmp_path / "syms.txt").write_text("RELIANCE\nTCS\n", encoding="utf-8")
    cfg = TradingConfig.from_dict({
        "mode": "paper", "symbols_file": str(tmp_path / "syms.txt"),
        "store_dir": str(tmp_path / "store"),
        "state_dir": str(tmp_path / "state"),
        "dashboard_dir": str(tmp_path / "dash"),
        "capital": {"deploy_today": 300_000, "max_daily_loss": 10_000}})
    eng = ProductionEngine(cfg, state=MarketState(store,
                                                    ["RELIANCE", "TCS"],
                                                    ["15m"]))
    eng.portfolio.add_position(Position(
        position_id="p1", symbol="RELIANCE", strategy="orb_15m",
        timeframe="15m", quantity=100, entry_price=100.0,
        entry_ts="2026-07-20T04:00:00+00:00", stop=95.0, initial_stop=95.0,
        target=110.0, open_quantity=100, last_price=104.0))
    eng.portfolio.realized_pnl = -250.0
    return eng


def _snapshots(engine, tmp_path=None):
    engine.exporter.export()
    out = {}
    for name in ("live", "portfolio", "performance", "positions", "system"):
        out[name] = json.loads(
            (engine.exporter.dir / f"{name}.json").read_text())["data"]
    return out


# ======================================== the same number in every panel

def test_pnl_and_capital_agree_across_every_panel(engine):
    snaps = _snapshots(engine)
    truth = engine.risk.risk_state(engine.portfolio)
    live, folio = snaps["live"], snaps["portfolio"]

    for field, expected in [
        ("realized_pnl", truth.realized_pnl),
        ("unrealized_pnl", truth.unrealized_pnl),
        ("deployed_capital", truth.deployed_capital),
        ("available_capital", truth.available_capital),
        ("open_risk", truth.open_risk),
    ]:
        assert live[field] == pytest.approx(round(expected, 2)), field
        assert folio[field] == pytest.approx(round(expected, 2)), field
        assert live[field] == folio[field], f"{field} differs between panels"


def test_portfolio_value_has_ONE_meaning(engine):
    """It meant the static allowance in one panel and the live mark in another.
    Both are real quantities; only one may own the name."""
    snaps = _snapshots(engine)
    truth = engine.risk.risk_state(engine.portfolio)
    assert snaps["live"]["portfolio_value"] == \
        snaps["portfolio"]["portfolio_value"]
    assert snaps["live"]["portfolio_value"] == \
        pytest.approx(round(truth.portfolio_value, 2))
    # the allowance is still available, under a name that says what it is
    assert snaps["portfolio"]["capital_base"] == 300_000


def test_open_risk_is_not_recomputed_anywhere(engine):
    """performance.json used to restate the risk engine's own formula."""
    snaps = _snapshots(engine)
    expected = sum(position_open_risk(p)
                   for p in engine.portfolio.open_positions())
    assert snaps["performance"]["open_risk"] == pytest.approx(round(expected, 2))
    assert snaps["portfolio"]["open_risk"] == pytest.approx(round(expected, 2))
    assert snaps["live"]["open_risk"] == pytest.approx(round(expected, 2))


def test_position_pnl_comes_from_the_position_object(engine):
    snaps = _snapshots(engine)
    pos = engine.portfolio.open_positions()[0]
    expected = round(pos.unrealized(pos.last_price), 2)
    assert snaps["positions"]["positions"][0]["pnl"] == pytest.approx(expected)
    assert snaps["live"]["positions"][0]["pnl"] == pytest.approx(expected)


def test_timeframes_are_the_engine_s_resolved_set(engine):
    snaps = _snapshots(engine)
    assert snaps["live"]["timing"]["timeframes"] == engine.timeframes


def test_position_count_agrees_everywhere(engine):
    snaps = _snapshots(engine)
    n = engine.portfolio.open_count()
    assert snaps["live"]["position_count"] == n
    assert snaps["positions"]["count"] == n
    assert snaps["portfolio"]["open_positions"] == n


# =================================== the panels track the engine as it moves

def test_panels_follow_a_price_change_together(engine):
    """Mark the position higher and every panel must move to the same new
    numbers - not merely be internally consistent once."""
    engine.portfolio.mark({"RELIANCE": 108.0})
    snaps = _snapshots(engine)
    truth = engine.risk.risk_state(engine.portfolio)
    assert snaps["live"]["unrealized_pnl"] == \
        pytest.approx(round(truth.unrealized_pnl, 2))
    assert snaps["portfolio"]["unrealized_pnl"] == \
        snaps["live"]["unrealized_pnl"]
    assert snaps["live"]["positions"][0]["current_price"] == 108.0
    assert snaps["positions"]["positions"][0]["current_price"] == 108.0


def test_panels_follow_a_stop_move_together(engine):
    pos = engine.portfolio.open_positions()[0]
    pos.stop = 99.0
    pos.trailed = True
    engine.portfolio.persist()
    snaps = _snapshots(engine)
    expected = round(position_open_risk(pos), 2)
    assert snaps["live"]["open_risk"] == pytest.approx(expected)
    assert snaps["portfolio"]["open_risk"] == pytest.approx(expected)
    assert snaps["performance"]["open_risk"] == pytest.approx(expected)
    assert snaps["positions"]["positions"][0]["stop_loss"] == 99.0
