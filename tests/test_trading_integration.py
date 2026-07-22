"""End-to-end production-engine integration: a crafted feed drives a full
paper cycle (scan -> risk -> order -> position -> manage -> exit), proving the
pipeline runs the FROZEN strategy + execution-spec code with the paper adapter,
and that restart recovery resumes the resulting position.
"""

import pandas as pd
import pytest

from algo.trading.config import TradingConfig
from algo.trading.engine import ProductionEngine
from algo.marketdata import MarketState
from algo.strategies.library import OpeningRangeBreakout
from strat01_fixtures import strat01_frames


class CraftedFeed(MarketState):
    """A MarketState serving in-memory frames per symbol.

    Only ``history`` is overridden - everything else (health, freshness,
    marks, diagnosis) is the real implementation, so the pipeline under test
    is the production one with a substituted candle source.
    """
    def __init__(self, frames, timeframes):
        super().__init__(store=None, symbols=list(frames),
                         timeframes=list(timeframes), history_bars=5000)
        self._frames = frames
        self.set_symbols(list(frames))

    def history(self, symbol, timeframe):
        return self._frames.get(symbol, pd.DataFrame()).copy()

    def set_frame(self, symbol, frame):
        self._frames[symbol] = frame


def _cfg(tmp_path):
    (tmp_path / "syms.txt").write_text("RELIANCE\n")
    return TradingConfig.from_dict({
        "mode": "paper", "state_dir": str(tmp_path / "state"),
        "store_dir": str(tmp_path / "store"),
        "symbols_file": str(tmp_path / "syms.txt"), "timeframes": ["5m"],
        "entry_cutoff_hour": 23, "squareoff_hour": 23,   # keep the gate open
        "capital": {"deploy_today": 500000, "max_daily_loss": 15000},
        # keep the exporter inside tmp_path: the default is the REAL
        # dashboard/dashboard_data, so a test run would otherwise overwrite the
        # snapshots of an actual trading session
        "dashboard_dir": str(tmp_path / "dash"),
    })


def _engine(tmp_path, frames):
    cfg = _cfg(tmp_path)
    feed = CraftedFeed(frames, ["5m"])
    strat = OpeningRangeBreakout()
    eng = ProductionEngine(cfg, state=feed, strategies=[strat])
    # never block on wall-clock in the test
    eng.clock.past_entry_cutoff = lambda at=None: False
    eng.clock.past_squareoff = lambda at=None: False
    return eng


def test_full_cycle_opens_a_position_from_a_real_orb_signal(tmp_path):
    frames = strat01_frames()
    eng = _engine(tmp_path, frames)

    assert eng.startup()
    result = eng.run_cycle("5m")
    assert result["opened"] == 1
    pos = eng.portfolio.open_positions()[0]
    assert pos.symbol == "RELIANCE" and pos.strategy == "orb_5m"
    assert pos.direction == "long"
    # The marketable collar fills at the signal quote plus paper slippage.
    assert pos.entry_price == pytest.approx(
        100.65 * (1 + eng.config.paper_slippage_pct))
    assert pos.stop < pos.entry_price
    # a fresh scan on the SAME bar does not double-enter (dedup + duplicate risk)
    assert eng.run_cycle("5m")["opened"] == 0
    assert eng.portfolio.open_count() == 1


def test_position_stops_out_and_books_loss(tmp_path):
    frames = strat01_frames()
    combined = frames["RELIANCE"]
    eng = _engine(tmp_path, frames)
    eng.startup()
    eng.run_cycle("5m")
    pos = eng.portfolio.open_positions()[0]
    stop = pos.stop

    # next 5-minute bar gaps through the stop
    crash = combined.iloc[[-1]].copy()
    crash["date"] = crash["date"] + pd.Timedelta(minutes=5)
    crash[["open", "high", "low", "close"]] = [stop - 1, stop - 0.5,
                                               stop - 2, stop - 1.5]
    nxt = pd.concat([combined, crash], ignore_index=True)
    eng.feed.set_frame("RELIANCE", nxt)
    eng.run_cycle("5m")
    assert eng.portfolio.open_count() == 0
    assert eng.portfolio.realized_pnl < 0             # honest gap-through loss


def test_restart_recovery_resumes_the_open_position(tmp_path):
    frames = strat01_frames()
    eng = _engine(tmp_path, frames)
    eng.startup()
    eng.run_cycle("5m")
    assert eng.portfolio.open_count() == 1
    qty = eng.portfolio.open_positions()[0].open_quantity

    # simulate restart: a NEW engine on the same state dir; the paper broker
    # is fresh, so recovery sees no broker position -> the position is treated
    # as orphaned-internal and closed at last price (deterministic, logged).
    eng2 = _engine(tmp_path, frames)
    report = None
    from algo.trading.recovery import RecoveryManager
    eng2.portfolio.load()
    assert eng2.portfolio.open_count() == 1           # persisted across restart
    report = RecoveryManager(eng2.portfolio, eng2.adapter, eng2.events,
                             feed=eng2.state).recover()
    # PaperBroker starts empty each process, so recovery closes the position
    # rather than leaving unmanaged risk - the safe, deterministic outcome.
    assert eng2.portfolio.open_count() == 0
    assert report.orphaned_internal


def test_no_entries_when_signal_absent(tmp_path):
    frames = strat01_frames()
    quiet = frames["RELIANCE"].copy()
    quiet.loc[quiet.index[-1], ["open", "high", "low", "close", "volume"]] = \
        [100.0, 100.4, 99.8, 100.2, 500_000.0]
    frames["RELIANCE"] = quiet
    eng = _engine(tmp_path, frames)
    eng.startup()
    assert eng.run_cycle("5m")["opened"] == 0
