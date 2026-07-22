"""Live scanning (§2): the scanner must act on the NEWEST completed candle.

Two properties, and they pull in opposite directions:

  * a bar must be evaluated at most ONCE, so a re-entrant tick inside the same
    bar cannot re-fire a signal that was already acted on; and
  * once a NEWER bar arrives, the scanner must move to it immediately and never
    keep acting on the older one.

``pytest -s`` prints an end-to-end trace of one symbol from new candle through
scanner, strategy, decision and dashboard.
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

TF = "15m"
SYMBOL = "RELIANCE"
DAY = "2026-07-20"                                   # a Monday
STRAT = "orb_15m"


def _day(date, breakout=False):
    """One session shaped to fire (or not fire) orb_15m on its LAST bar: a
    15-minute opening range, then a volume-confirmed close above the OR high.
    Same construction as tests/test_trading_integration.py."""
    times = pd.date_range(f"{date} 09:15", periods=8, freq="15min",
                          tz="Asia/Kolkata")
    if breakout:
        close = [100, 100.2, 100.1, 100.3, 100.2, 100.4, 100.5, 101.5]
        high = [100.5, 100.6, 100.5, 100.6, 100.5, 100.7, 100.8, 101.8]
        vol = [1000, 1000, 1000, 1000, 1000, 1000, 1000, 5000]
    else:
        close = [100, 100.1, 100.05, 100.1, 100.05, 100.1, 100.05, 100.1]
        high = [100.3] * 8
        vol = [1000] * 8
    low = [c - 0.4 for c in close]
    op = [close[0]] + close[:-1]
    return pd.DataFrame({"date": times, "open": op, "high": high, "low": low,
                         "close": close, "volume": vol})


def _session(n_days=5, breakout_on_last=False):
    """``n_days`` quiet sessions, optionally ending in a breakout session.
    orb_15m needs 25+ bars of history, so several days are required."""
    days = pd.bdate_range("2026-07-13", periods=n_days)      # ends 2026-07-17
    frames = [_day(d.date(), breakout=False) for d in days[:-1]]
    frames.append(_day(days[-1].date(), breakout=breakout_on_last))
    return pd.concat(frames, ignore_index=True)


@pytest.fixture
def engine(tmp_path):
    store = MarketDataStore(tmp_path / "store")
    (tmp_path / "syms.txt").write_text(f"{SYMBOL}\n", encoding="utf-8")
    cfg = TradingConfig.from_dict({
        "mode": "paper", "symbols_file": str(tmp_path / "syms.txt"),
        "store_dir": str(tmp_path / "store"),
        "state_dir": str(tmp_path / "state"),
        "dashboard_dir": str(tmp_path / "dash"),
        "capital": {"deploy_today": 300_000, "max_daily_loss": 10_000,
                    "min_trade_allocation": 0.0}})
    eng = ProductionEngine(cfg, state=MarketState(store, [SYMBOL], [TF]))
    eng.clock.now = lambda: datetime(2026, 7, 20, 12, 7, tzinfo=IST)
    eng.clock.past_entry_cutoff = lambda at=None: False
    eng.clock.past_squareoff = lambda at=None: False
    return eng


def _key(engine):
    return engine.orchestrator._last_eval.get((STRAT, SYMBOL, TF))


def _last_bar(engine):
    return pd.Timestamp(engine.feed.history(SYMBOL, TF)["date"].iloc[-1])


# ========================================================= the two properties

def test_a_bar_is_evaluated_at_most_once(engine):
    engine.feed.store.write(SYMBOL, TF, _session(5))
    engine.orchestrator.evaluate(TF)
    first = _key(engine)
    assert first == _last_bar(engine)

    seen = []
    strat = next(s for s in engine.strategies if s.name == STRAT)
    original = strat.entry_signal
    strat.entry_signal = lambda df: seen.append(1) or original(df)
    engine.orchestrator.evaluate(TF)
    engine.orchestrator.evaluate(TF)
    assert seen == [], "the same bar was re-evaluated"
    assert _key(engine) == first


def test_the_scanner_moves_to_a_newer_bar_immediately(engine):
    engine.feed.store.write(SYMBOL, TF, _session(5))
    engine.orchestrator.evaluate(TF)
    old = _key(engine)

    engine.feed.store.write(SYMBOL, TF, _session(6).tail(8))
    new_bar = _last_bar(engine)
    assert new_bar > old

    engine.orchestrator.evaluate(TF)
    assert _key(engine) == new_bar, "the scanner stayed on the older candle"


def test_the_scanner_never_regresses_to_an_older_bar(engine):
    engine.feed.store.write(SYMBOL, TF, _session(6))
    engine.orchestrator.evaluate(TF)
    newest = _key(engine)
    # a late-arriving OLD bar must not pull the scanner backwards
    engine.feed.store.write(SYMBOL, TF, _session(6).head(3))
    engine.orchestrator.evaluate(TF)
    assert _key(engine) == newest


def test_only_the_newest_bar_can_fire_a_signal(engine):
    """A breakout three bars ago must NOT be traded now - the entry price and
    the stop would both belong to a bar that has already passed."""
    engine.feed.store.write(SYMBOL, TF,
                            pd.concat([_session(5, breakout_on_last=True),
                                       _day("2026-07-20")],
                                      ignore_index=True))
    signals = engine.orchestrator.evaluate(TF)
    assert signals == []


def test_a_breakout_on_the_newest_bar_does_fire(engine):
    frame = _session(5, breakout_on_last=True)
    engine.feed.store.write(SYMBOL, TF, frame)
    signals = engine.orchestrator.evaluate(TF)
    assert [s.strategy for s in signals if s.strategy == STRAT]
    sig = next(s for s in signals if s.strategy == STRAT)
    assert sig.bar_time == pd.Timestamp(frame["date"].iloc[-1])
    assert sig.entry_ref == pytest.approx(float(frame["close"].iloc[-1]))


def test_restarting_does_not_re_trade_a_historical_signal(engine, tmp_path):
    """The dedup map is in memory, so a restart re-evaluates - which must be
    safe: only the newest bar can fire, and it has already been acted on."""
    engine.feed.store.write(SYMBOL, TF,
                            pd.concat([_session(5, breakout_on_last=True),
                                       _day("2026-07-20")],
                                      ignore_index=True))
    assert engine.orchestrator.evaluate(TF) == []
    engine.orchestrator._last_eval.clear()            # simulate a restart
    assert engine.orchestrator.evaluate(TF) == []


# ============================================ end-to-end trace of one symbol

def test_trace_one_symbol_end_to_end(engine, capsys):
    """The §2 deliverable: new candle -> scanner -> strategy -> decision ->
    dashboard, with the bar identity carried through every stage."""
    frame = _session(5, breakout_on_last=True)
    engine.feed.store.write(SYMBOL, TF, frame)
    new_bar = pd.Timestamp(frame["date"].iloc[-1])

    signals = engine.orchestrator.evaluate(TF)
    sig = next(s for s in signals if s.strategy == STRAT)

    engine.orchestrator._last_eval.clear()            # let run_cycle re-scan
    result = engine.run_cycle(TF)
    pos = engine.portfolio.open_positions()[0]
    engine.exporter.export()
    row = json.loads((engine.exporter.dir / "positions.json").read_text()
                     )["data"]["positions"][0]

    with capsys.disabled():
        print(f"""

  END-TO-END TRACE — {SYMBOL} on {TF}
  {'-' * 68}
  1 NEW CANDLE      stored bar open   {new_bar}
                    close             {frame['close'].iloc[-1]:.2f}
                    volume            {frame['volume'].iloc[-1]:,.0f}
  2 SCANNER         history served    {len(engine.feed.history(SYMBOL, TF))} bars
                    newest bar        {_last_bar(engine)}
                    dedup key         {_key(engine)}
  3 STRATEGY        fired             {sig.strategy}
                    bar_time          {sig.bar_time}   (== the new candle)
  4 SIGNAL          entry_ref         {sig.entry_ref:.2f}
                    stop              {sig.stop:.2f}
                    target            {sig.target}
                    risk/unit         {sig.risk_per_unit:.2f}
  5 DECISION        opened            {result['opened']} position(s)
                    qty               {pos.quantity:g}
                    fill              {pos.entry_price:.2f}
                    position_id       {pos.position_id}
  6 DASHBOARD       symbol            {row['symbol']}
                    entry             {row['entry_price']}
                    stop              {row['stop_loss']}
                    state             {row['state']}
                    next action       {row['next_action'][:44]}...
  {'-' * 68}
  the SAME bar timestamp appears at every stage: {new_bar}
""")

    assert sig.bar_time == new_bar
    assert str(new_bar.value) in pos.position_id      # id carries the bar
    assert row["entry_price"] == pytest.approx(round(pos.entry_price, 2))
    assert result["opened"] == 1


# ================================== the scanner is quiet, not wrong, when stale

def test_a_stale_store_makes_the_scanner_quiet_and_says_so(engine):
    """If the store stops updating, the scanner correctly emits nothing - but
    silence must not be the only signal, or a dead feed looks like a calm
    market. The freshness report is what distinguishes them."""
    engine.feed.store.write(SYMBOL, TF, _session(5, breakout_on_last=True))
    engine.orchestrator.evaluate(TF)                  # consumes the breakout
    assert engine.orchestrator.evaluate(TF) == []     # quiet on repeat

    report = engine.feed.freshness(TF, clock=engine.clock)
    assert report.status == "STALE"
    assert "STALE DATA" in report.headline()
