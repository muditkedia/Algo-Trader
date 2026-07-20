"""Exit-engine verification matrix (§8) and unconditional square-off (§9).

Every registered intraday strategy is driven through the REAL ``TradeManager``
with bars constructed to hit each exit path, so the matrix records what the
engine actually does rather than what the ExecutionSpec says it should.

Checked per strategy: entry levels, stop, target, partial, breakeven,
trailing, time exit and square-off. ``pytest -s`` prints the matrix.
"""

import pandas as pd
import pytest

from algo.core.enums import HoldingScope
from algo.risk.engine import RiskParams
from algo.strategies.library import ALL_STRATEGIES
from algo.trading.models import Position
from algo.trading.trademanager import TradeManager

INTRADAY = [c for c in ALL_STRATEGIES
            if c.meta.enabled and c.meta.holding_scope == HoldingScope.INTRADAY]
SWING = [c for c in ALL_STRATEGIES
         if c.meta.enabled and c.meta.holding_scope != HoldingScope.INTRADAY]

ENTRY, STOP, TARGET, TARGET2 = 100.0, 95.0, 110.0, 120.0


def _bar(o, h, l, c, **extra):
    row = {"open": o, "high": h, "low": l, "close": c, "volume": 1000}
    row.update(extra)
    return pd.Series(row)


def _position(strategy, spec, *, stop=STOP, target=TARGET, target2=TARGET2,
              qty=100.0, partial_done=False, trailed=False):
    return Position(
        position_id=f"{strategy}-1", symbol="RELIANCE", strategy=strategy,
        timeframe=spec_timeframe(strategy), quantity=qty, entry_price=ENTRY,
        entry_ts="2026-07-20T04:00:00+00:00", stop=stop, initial_stop=STOP,
        target=target, target2=target2,
        partial_fraction=spec.partial_fraction, open_quantity=qty,
        partial_done=partial_done, trailed=trailed, last_price=ENTRY,
        atr_at_entry=2.0, trail_mode=spec.trail)


def spec_timeframe(name):
    for cls in ALL_STRATEGIES:
        if cls.meta.name == name:
            return cls.meta.timeframe
    return "15m"


def _tm(cls):
    return TradeManager({cls.meta.name: cls.execution}, params=RiskParams())


# ============================================================== the matrix

def probe(cls) -> dict:
    """Exercise every exit path for one strategy and record the outcome."""
    spec = cls.execution
    name = cls.meta.name
    tm = _tm(cls)
    out = {"name": name, "timeframe": cls.meta.timeframe,
           "intraday": spec.intraday}

    # --- stop: a bar whose low pierces the stop must exit, stop-first
    d = tm.manage(_position(name, spec), _bar(101, 102, 94, 101), spec=spec)
    out["stop"] = d.action == "exit" and d.reason in (
        "stop_loss", "trailing_stop", "breakeven_stop")

    # --- stop takes precedence over target on the SAME bar (pessimistic)
    d = tm.manage(_position(name, spec), _bar(101, 115, 94, 112), spec=spec)
    out["stop_first"] = d.action == "exit" and d.reason == "stop_loss"

    # --- gap through the stop fills at the OPEN, not the stop level
    d = tm.manage(_position(name, spec), _bar(90, 92, 89, 91), spec=spec)
    out["gap_honest"] = (d.action == "exit" and d.price == 90.0
                         and d.gap_fill)

    # --- target / partial
    pos = _position(name, spec)
    d = tm.manage(pos, _bar(101, 112, 100, 111), spec=spec)
    if spec.target_kind == "none":
        out["target"] = "n/a"
        out["partial"] = "n/a"
        out["breakeven"] = "n/a"
    else:
        if spec.partial_fraction > 0:
            out["target"] = d.action == "partial"
            out["partial"] = (d.action == "partial"
                              and d.partial_qty == pytest.approx(
                                  pos.open_quantity * spec.partial_fraction))
            # breakeven: the partial moves the stop to entry
            out["breakeven"] = d.new_stop == pytest.approx(ENTRY)
        else:
            out["target"] = d.action == "exit" and d.reason == "target"
            out["partial"] = "n/a"
            out["breakeven"] = "n/a"

    # --- trailing
    if spec.trail == "none":
        out["trailing"] = "n/a"
    else:
        extra = {}
        if spec.trail == "column" and spec.trail_col:
            extra[spec.trail_col] = 104.0
        d = tm.manage(_position(name, spec), _bar(106, 108, 105, 107, **extra),
                      spec=spec)
        out["trailing"] = d.action == "trail" and d.new_stop > STOP

    # --- trailing never widens the stop (ratchet only)
    if spec.trail == "column" and spec.trail_col:
        d = tm.manage(_position(name, spec, stop=104.0),
                      _bar(106, 108, 105, 107, **{spec.trail_col: 99.0}),
                      spec=spec)
        out["ratchet_only"] = d.action == "hold"
    elif spec.trail == "chandelier":
        d = tm.manage(_position(name, spec, stop=106.0),
                      _bar(106.5, 107, 106.2, 106.8), spec=spec)
        out["ratchet_only"] = d.action in ("hold", "exit")
    else:
        out["ratchet_only"] = "n/a"

    # --- square-off / time exit
    d = tm.manage(_position(name, spec), _bar(101, 102, 100, 101.5),
                  spec=spec, past_squareoff=True)
    if spec.intraday:
        out["squareoff"] = (d.action == "exit"
                            and d.reason == "session_squareoff"
                            and d.price == 101.5)
    else:
        out["squareoff"] = "n/a"
    out["time_exit"] = ("session 15:15" if spec.intraday
                        else f"{spec.max_hold_bars} bars")
    return out


@pytest.mark.parametrize("cls", INTRADAY, ids=lambda c: c.meta.name)
def test_every_intraday_strategy_exits_as_declared(cls):
    result = probe(cls)
    failures = [k for k, v in result.items() if v is False]
    assert not failures, f"{result['name']}: {failures} did not behave as declared"


def test_print_the_verification_matrix(capsys):
    """The §8 deliverable. Run with -s to see it."""
    rows = [probe(c) for c in INTRADAY]
    cols = ["stop", "stop_first", "gap_honest", "target", "partial",
            "breakeven", "trailing", "ratchet_only", "squareoff"]
    mark = {True: "Y", False: "FAIL", "n/a": "-"}
    with capsys.disabled():
        print("\n\n  EXIT ENGINE VERIFICATION MATRIX "
              f"({len(rows)} intraday strategies)\n")
        head = f"  {'strategy':22} {'tf':4} " + " ".join(
            f"{c[:9]:>10}" for c in cols)
        print(head)
        print("  " + "-" * (len(head) - 2))
        for r in rows:
            line = f"  {r['name']:22} {r['timeframe']:4} " + " ".join(
                f"{mark.get(r[c], r[c]):>10}" for c in cols)
            print(line)
        print(f"\n  time exit: every intraday spec squares off at the session "
              f"cutoff; swing specs use max_hold_bars")
        print(f"  swing strategies NOT in the intraday engine: {len(SWING)}")
    assert all(v is not False for r in rows for v in r.values())


# =========================================== §9 unconditional square-off

@pytest.mark.parametrize("cls", INTRADAY, ids=lambda c: c.meta.name)
def test_squareoff_is_unconditional(cls):
    """15:15 must close an intraday position regardless of profit, loss,
    target, trailing state or partial state."""
    spec = cls.execution
    if not spec.intraday:
        pytest.skip("swing spec")
    tm = _tm(cls)
    variants = {
        "in profit": _position(cls.meta.name, spec),
        "in loss": _position(cls.meta.name, spec, stop=80.0),
        "after a partial": _position(cls.meta.name, spec, partial_done=True),
        "while trailing": _position(cls.meta.name, spec, stop=99.0,
                                    trailed=True),
        "no target": _position(cls.meta.name, spec, target=None, target2=None),
    }
    for label, pos in variants.items():
        # a quiet bar that hits neither stop nor target
        d = tm.manage(pos, _bar(101, 102, 100, 101.5), spec=spec,
                      past_squareoff=True)
        assert d.action == "exit", f"{label}: {d.action}"
        assert d.reason == "session_squareoff", f"{label}: {d.reason}"


def test_squareoff_does_not_override_a_stop_already_hit(cls=None):
    """Square-off is unconditional but not FIRST: if the bar already breached
    the stop, the honest fill is the stop, not the close. Reversing that would
    report a better price than the position could have got."""
    cls = INTRADAY[0]
    spec = cls.execution
    d = _tm(cls).manage(_position(cls.meta.name, spec),
                        _bar(101, 102, 90, 101.5), spec=spec,
                        past_squareoff=True)
    assert d.action == "exit" and d.reason == "stop_loss"


def test_every_intraday_spec_forbids_overnight():
    for cls in INTRADAY:
        assert cls.execution.intraday is True, cls.meta.name
        assert cls.execution.allow_overnight is False, cls.meta.name


# =============================== §9 square-off through the WHOLE engine

def _engine(tmp_path, frames):
    """A real ProductionEngine over crafted history."""
    from algo.trading.config import TradingConfig
    from algo.trading.engine import ProductionEngine
    from algo.data.store import MarketDataStore
    from algo.marketdata import MarketState

    store = MarketDataStore(tmp_path / "store")
    for symbol, frame in frames.items():
        store.write(symbol, "15m", frame)
    (tmp_path / "syms.txt").write_text("\n".join(frames) or "RELIANCE",
                                       encoding="utf-8")
    cfg = TradingConfig.from_dict({
        "mode": "paper", "symbols_file": str(tmp_path / "syms.txt"),
        "store_dir": str(tmp_path / "store"),
        "state_dir": str(tmp_path / "state"),
        "dashboard_dir": str(tmp_path / "dash"),
        "capital": {"deploy_today": 300_000, "max_daily_loss": 10_000}})
    feed = MarketState(store, list(frames) or ["RELIANCE"], ["15m"])
    engine = ProductionEngine(cfg, state=feed)
    return engine


def _history(symbol="RELIANCE", n=30, price=100.0):
    opens = pd.date_range("2026-07-20 09:15", periods=n, freq="15min",
                          tz="Asia/Kolkata")
    return pd.DataFrame({"date": opens, "open": price, "high": price + 1,
                         "low": price - 1, "close": price, "volume": 1000})


def _open_position(engine, symbol="RELIANCE", strategy="orb_15m", **kw):
    spec = engine.specs[strategy]
    pos = _position(strategy, spec, **kw)
    pos.symbol = symbol
    engine.portfolio.add_position(pos)
    engine.adapter.update_quotes({symbol: ENTRY})
    return pos


@pytest.mark.parametrize("label, kw", [
    ("in profit", {}),
    ("in loss", {"stop": 80.0}),
    ("after a partial", {"partial_done": True}),
    ("while trailing", {"stop": 99.0, "trailed": True}),
    ("no target", {"target": None, "target2": None}),
])
def test_engine_squares_off_at_the_cutoff(tmp_path, label, kw):
    """The §9 requirement is that NO intraday position survives the cutoff.

    Which exit fires is a separate question: a stop or target already breached
    by the bar takes precedence (stop-first, the honest fill), so the reason
    may legitimately be a stop rather than the square-off. What must never
    happen is the position remaining open.
    """
    engine = _engine(tmp_path, {"RELIANCE": _history()})
    _open_position(engine, **kw)
    engine.clock.past_squareoff = lambda at=None: True
    engine.run_cycle("15m", scan=False)
    assert engine.portfolio.open_count() == 0, label
    assert engine.portfolio.closed_trades[-1]["exit_reason"] in (
        "session_squareoff", "stop_loss", "trailing_stop", "breakeven_stop",
        "target"), label


def test_a_quiet_bar_at_the_cutoff_exits_as_session_squareoff(tmp_path):
    """With neither stop nor target touched, the reason must be the
    square-off itself - not silence."""
    engine = _engine(tmp_path, {"RELIANCE": _history(price=100.0)})
    _open_position(engine, stop=80.0, target=None, target2=None)
    engine.clock.past_squareoff = lambda at=None: True
    engine.run_cycle("15m", scan=False)
    assert engine.portfolio.closed_trades[-1]["exit_reason"] == \
        "session_squareoff"


def test_squareoff_still_happens_when_the_symbol_has_NO_market_data(tmp_path):
    """A position whose data feed died must still be closed at the cutoff.

    ``_manage_open`` skips a symbol with no bars (it cannot evaluate a stop
    without a bar), so square-off must NOT depend on that path - otherwise a
    dead feed would silently carry a position overnight, which for an MIS
    intraday product means a forced broker square-off at an unknown price.
    """
    engine = _engine(tmp_path, {"RELIANCE": _history()})
    pos = _open_position(engine, symbol="GHOST")     # no bars stored for GHOST
    engine.feed.symbols = ["RELIANCE", "GHOST"]
    assert engine.feed.history("GHOST", "15m").empty  # precondition
    engine.clock.past_squareoff = lambda at=None: True
    engine.run_cycle("15m", scan=False)
    assert engine.portfolio.open_count() == 0
    record = engine.portfolio.closed_trades[-1]
    assert record["exit_reason"] == "session_squareoff"
    # priced from the last known mark, never from nothing
    assert record["exit_price"] > 0


def test_squareoff_happens_even_when_the_risk_engine_has_tripped(tmp_path):
    engine = _engine(tmp_path, {"RELIANCE": _history()})
    _open_position(engine)
    engine.risk.trip("test halt")
    engine.clock.past_squareoff = lambda at=None: True
    engine.run_cycle("15m", scan=False)
    assert engine.portfolio.open_count() == 0


def test_no_new_entries_are_taken_after_the_cutoff(tmp_path):
    engine = _engine(tmp_path, {"RELIANCE": _history()})
    engine.clock.past_squareoff = lambda at=None: True
    engine.clock.past_entry_cutoff = lambda at=None: True
    result = engine.run_cycle("15m", scan=True)
    assert result["opened"] == 0


def test_no_swing_strategy_reaches_the_intraday_engine():
    """The intraday engine squares off at 15:15; a spec that needs 126 days to
    reach its horizon would be closed on entry day, every day."""
    from algo.trading.engine import load_intraday_strategies
    loaded = {s.name for s in load_intraday_strategies()}
    swing = {c.meta.name for c in SWING}
    assert not (loaded & swing)
    assert loaded == {c.meta.name for c in INTRADAY}
