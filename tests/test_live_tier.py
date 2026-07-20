"""Fast/slow dashboard tiers (§6), execution plan (§7), square-off countdown
(§9) and market status (§10).

The dashboard used to redraw everything every two seconds. Prices and P&L do
change that fast; the scanner, signals, explainability, timeline and logs
cannot change between completed candles, so redrawing them was both wasted work
and visible flicker. The split also gives the operator something the wall clock
never did: how long until the scanner next acts, and until every intraday
position is closed.
"""

import json
from datetime import datetime

import pandas as pd
import pytest

from algo.data.store import MarketDataStore
from algo.trading.clock import IST
from algo.trading.config import TradingConfig
from algo.trading.engine import ProductionEngine
from algo.marketdata import MarketState
from algo.marketdata import NullSource, Quote
from algo.trading.models import Position

OPEN_AT = datetime(2026, 7, 20, 11, 7, tzinfo=IST)      # Monday, mid-session


def _history(n=30, price=100.0):
    opens = pd.date_range("2026-07-20 09:15", periods=n, freq="15min",
                          tz="Asia/Kolkata")
    return pd.DataFrame({"date": opens, "open": price, "high": price + 1,
                         "low": price - 1, "close": price, "volume": 1000})


@pytest.fixture
def engine(tmp_path):
    store = MarketDataStore(tmp_path / "store")
    store.write("RELIANCE", "15m", _history())
    (tmp_path / "syms.txt").write_text("RELIANCE\n", encoding="utf-8")
    cfg = TradingConfig.from_dict({
        "mode": "paper", "symbols_file": str(tmp_path / "syms.txt"),
        "store_dir": str(tmp_path / "store"),
        "state_dir": str(tmp_path / "state"),
        "dashboard_dir": str(tmp_path / "dash"),
        "capital": {"deploy_today": 300_000, "max_daily_loss": 10_000}})
    eng = ProductionEngine(cfg, state=MarketState(store, ["RELIANCE"],
                                                    ["15m"]))
    eng.clock.now = lambda: OPEN_AT
    return eng


def _add(engine, **kw):
    spec = engine.specs["cpr_breakout_15m"]      # the one with a partial
    defaults = dict(position_id="p1", symbol="RELIANCE",
                    strategy="cpr_breakout_15m", timeframe="15m", quantity=100,
                    entry_price=100.0, entry_ts="2026-07-20T04:00:00+00:00",
                    stop=95.0, initial_stop=95.0, target=110.0, target2=120.0,
                    partial_fraction=spec.partial_fraction, open_quantity=100,
                    last_price=104.0)
    defaults.update(kw)
    pos = Position(**defaults)
    engine.portfolio.add_position(pos)
    return pos


def _read(engine, name):
    return json.loads((engine.exporter.dir / f"{name}.json").read_text())["data"]


# ============================================================== §6 the tiers

def test_the_fast_tier_writes_only_live_json(engine):
    engine.exporter.export()
    others = {n: (engine.exporter.dir / f"{n}.json").read_bytes()
              for n in ("scanner", "positions", "timeline", "logs")}
    engine.exporter.export_live()
    for name, before in others.items():
        assert (engine.exporter.dir / f"{name}.json").read_bytes() == before, \
            f"{name} was rewritten by the fast tier"


def test_the_fast_snapshot_stays_small(engine):
    _add(engine)
    engine.exporter.export_live()
    size = (engine.exporter.dir / "live.json").stat().st_size
    assert size < 8_000, f"live.json is {size} bytes - too big for 1s polling"


def test_the_fast_tier_carries_what_changes_per_second(engine):
    _add(engine)
    engine.exporter.export_live()
    live = _read(engine, "live")
    for key in ("positions", "portfolio_value", "unrealized_pnl",
                "realized_pnl", "total_pnl", "deployed_capital", "timing"):
        assert key in live, key
    assert live["positions"][0]["current_price"] == 104.0
    assert live["positions"][0]["value"] == pytest.approx(10_400.0)


def test_a_tick_with_nothing_due_does_not_scan(engine):
    """Ticks run at loop cadence; the heavy work stays on completed bars."""
    calls = {"evaluate": 0}
    engine.orchestrator.evaluate = lambda tf: calls.__setitem__(
        "evaluate", calls["evaluate"] + 1) or []
    _add(engine)
    engine.tick()                                  # first tick: bar completes
    calls["evaluate"] = 0
    for _ in range(5):                             # nothing new completes
        engine.tick()
    assert calls["evaluate"] == 0


def test_quote_cost_is_bounded_by_open_positions(engine):
    """Not by universe size: a 1000-symbol watchlist must cost the same here
    as a 10-symbol one, because only HELD symbols are marked."""
    quoted = []

    class Recorder(NullSource):
        name = "recorder"
        def status(self): return "OK"
        def fetch_quotes(self, symbols):
            quoted.append(list(symbols))
            return {}

    engine.marketdata.source = Recorder()
    engine.marketdata.transport.source = engine.marketdata.source
    engine.state.set_symbols([f"SYM{i}" for i in range(1000)])
    _add(engine)
    engine.tick()
    assert quoted == [["RELIANCE"]]


def test_the_fast_tier_does_not_rescan_the_watchlist_for_freshness(engine):
    """The exporter measures freshness itself when the engine has not run a
    cycle yet - which reads EVERY watchlist symbol. At the fast tier's cadence
    that reintroduced O(universe) cost in the one place designed to avoid it:
    2000 symbols cost 533 ms per call before the measurement was cached.
    """
    reads = []
    original = engine.state.freshness
    engine.state.freshness = lambda *a, **k: reads.append(1) or original(*a, **k)
    engine.state.set_symbols([f"SYM{i}" for i in range(500)])
    for _ in range(10):
        engine.exporter.export_live()
    assert len(reads) <= 1, f"freshness re-measured {len(reads)} times"


def test_the_engine_takes_over_freshness_once_it_has_run(engine):
    engine.exporter.export_live()               # exporter measures it
    assert engine.exporter._fallback_fresh is not None
    engine.run_cycle("15m", scan=False)         # engine now produces it
    engine.exporter.export_live()
    assert engine.exporter._fallback_fresh is None
    assert engine.exporter._freshness() is engine.freshness["15m"]


def test_a_broken_exporter_cannot_stop_a_cycle(engine):
    """Observation must not be able to affect trading. The exporter guards its
    own internals, but the invariant belongs to the ENGINE - otherwise the
    guarantee rests on the exporter being well-behaved."""
    _add(engine)
    for method in ("export", "export_live", "note_scanner"):
        setattr(engine.exporter, method,
                lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    engine.clock.past_squareoff = lambda at=None: True
    engine.run_cycle("15m", scan=False)          # must not raise
    assert engine.portfolio.open_count() == 0, "square-off was skipped"


def _quoting(engine, prices, fail=False):
    """Point the service at a source that returns ``prices`` for any quote."""
    calls = []

    class Quoting(NullSource):
        name = "quoting"
        def status(self): return "OK"
        def fetch_quotes(self, symbols):
            calls.append(list(symbols))
            if fail:
                raise RuntimeError("broker down")
            return {s: Quote(symbol=s, price=prices[s]) for s in symbols
                    if s in prices}

    engine.marketdata.source = Quoting()
    engine.marketdata.transport.source = engine.marketdata.source
    return calls


def test_quotes_are_polled_no_faster_than_configured(engine):
    calls = _quoting(engine, {})
    _add(engine)
    for _ in range(5):
        engine.tick()
    assert len(calls) == 1, "quote polling ignored live_quote_seconds"


def test_a_quote_failure_falls_back_to_the_stored_close(engine):
    """A dead quote endpoint must not blank the panel or raise: marking is
    display-only, so the last completed-bar close stands."""
    _quoting(engine, {}, fail=True)
    _add(engine)
    engine.tick()                                # must not raise
    live = _read(engine, "live")
    assert live["positions"][0]["price_source"] == "CANDLE"
    # the stored close, which is what "fall back to the candle" means
    assert live["positions"][0]["current_price"] == 100.0


def test_price_provenance_is_always_stated(engine):
    _quoting(engine, {"RELIANCE": 106.5})
    _add(engine)
    engine.tick()
    live = _read(engine, "live")
    assert live["positions"][0]["price_source"] == "LTP"
    assert live["positions"][0]["current_price"] == 106.5
    assert live["quotes_live"] is True


# ====================================================== §10 market status

def test_market_status_replaces_the_wall_clock(engine):
    engine.exporter.export_live()
    live = _read(engine, "live")
    assert "current_time_ist" not in live
    timing = live["timing"]
    assert timing["status"] == "OPEN" and timing["market_open"] is True


def test_next_candle_countdown_per_timeframe(engine):
    engine.exporter.export_live()
    timing = _read(engine, "live")["timing"]
    # 11:07 -> the 15m bar closes at 11:15, i.e. 8 minutes away
    assert timing["next_candle"]["15m"] == pytest.approx(8 * 60, abs=2)
    for tf in engine.timeframes:
        assert tf in timing["next_candle"]


def test_countdowns_to_squareoff_and_cutoff(engine):
    engine.exporter.export_live()
    timing = _read(engine, "live")["timing"]
    assert timing["squareoff_time"] == "15:15"
    assert timing["entry_cutoff_time"] == "15:00"
    # 11:07 -> 15:15 is 4h08m
    assert timing["seconds_to_squareoff"] == pytest.approx(4 * 3600 + 8 * 60,
                                                           abs=60)
    assert timing["seconds_to_entry_cutoff"] < timing["seconds_to_squareoff"]


def test_closed_market_reports_closed(tmp_path, engine):
    engine.clock.now = lambda: datetime(2026, 7, 19, 11, 0, tzinfo=IST)  # Sun
    engine.exporter.export_live()
    timing = _read(engine, "live")["timing"]
    assert timing["status"] == "CLOSED" and not timing["market_open"]
    assert timing["session_seconds_remaining"] == 0


# =================================== §7 execution plan / §9 square-off

def test_every_position_carries_a_deterministic_plan(engine):
    _add(engine)
    engine.exporter.export()
    row = _read(engine, "positions")["positions"][0]
    plan = row["execution_plan"]
    for key in ("state", "next_action", "triggers", "trailing",
                "squareoff_time"):
        assert key in plan, key
    kinds = [t["kind"] for t in plan["triggers"]]
    # the order mirrors TradeManager.manage: stop -> target -> square-off
    assert kinds[0] == "stop"
    assert "partial" in kinds            # cpr_breakout books 50% at target 1
    assert kinds[-1] in ("squareoff", "trail")
    assert "squareoff" in kinds


def test_the_plan_states_the_actual_levels(engine):
    _add(engine)
    engine.exporter.export()
    triggers = _read(engine, "positions")["positions"][0]["triggers"]
    stop = next(t for t in triggers if t["kind"] == "stop")
    partial = next(t for t in triggers if t["kind"] == "partial")
    assert stop["level"] == 95.0 and "95" in stop["condition"]
    assert partial["level"] == 110.0
    assert "50%" in partial["action"] and "breakeven" in partial["action"]


def test_the_plan_follows_the_position_state(engine):
    _add(engine, partial_done=True, stop=100.0)
    engine.exporter.export()
    row = _read(engine, "positions")["positions"][0]
    assert "remainder" in row["state"].lower()
    kinds = [t["kind"] for t in row["triggers"]]
    assert "partial" not in kinds          # already booked
    assert any(t["kind"] == "target" and t["level"] == 120.0
               for t in row["triggers"])


def test_every_intraday_position_shows_its_squareoff_countdown(engine):
    _add(engine)
    engine.exporter.export()
    row = _read(engine, "positions")["positions"][0]
    assert row["intraday"] is True
    assert row["squareoff_time"] == "15:15"
    assert row["seconds_to_squareoff"] > 0
    assert any(t["kind"] == "squareoff"
               and "regardless" in t["action"] for t in row["triggers"])


def test_an_orphan_position_is_told_to_square_off(engine):
    _add(engine, strategy="__orphan__", position_id="orphan")
    engine.exporter.export()
    row = _read(engine, "positions")["positions"][0]
    assert "Square off" in row["next_action"]
    assert row["execution_plan"]["triggers"][0]["kind"] == "squareoff"
