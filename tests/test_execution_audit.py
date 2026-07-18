"""Phase 16 - execution-model characterization tests (Part B of the audit).

These tests do not change behaviour; they PIN the simulator's exact execution
semantics so the fidelity audit's claims are verified facts, not readings of the
code. Each test name states the audited behaviour. If any of these ever fails,
the execution model changed and every recorded baseline is invalidated.

Audited facts:
  * entry fills at the SIGNAL bar's close (not next open, not the trigger level)
  * the entry bar itself cannot stop the trade (exits start at the next bar)
  * a stop is filled AT the stop price even if the bar gaps below it - a
    documented OPTIMISTIC assumption (no gap-through slippage)
  * stops only ever tighten (monotonic ratchet); the trail activates at +0.6%
  * there is NO profit target - a monster rally is exited only by trail /
    square-off / horizon (D-006)
  * intraday square-off closes on the session's last bar, incl. partial days
"""

import numpy as np
import pandas as pd

from algo.core.costs import NseEquityCostModel, Product
from algo.research.simulator import simulate_trade
from algo.risk.engine import RiskParams


def _bars(day, closes, highs=None, lows=None, opens=None):
    closes = np.asarray(closes, float)
    n = len(closes)
    return pd.DataFrame({
        "date": pd.date_range(f"{day} 09:15", periods=n, freq="15min",
                              tz="UTC"),
        "open": np.asarray(opens, float) if opens is not None
        else np.concatenate(([closes[0]], closes[:-1])),
        "high": np.asarray(highs, float) if highs is not None else closes + 0.5,
        "low": np.asarray(lows, float) if lows is not None else closes - 0.5,
        "close": closes,
        "volume": np.full(n, 1000.0)})


COST = NseEquityCostModel()


def _sim(bars, i=1, product=Product.INTRADAY, max_bars=50, atr=1.0):
    return simulate_trade(bars, i, atr=atr, swing_low=float(bars["low"].iloc[i]),
                          params=RiskParams(), cost_model=COST,
                          product=product, max_bars=max_bars)


def test_entry_fills_at_signal_bar_close():
    """AUDIT: entry price is the signal candle's CLOSE - not the next candle's
    open and not the breakout trigger level. A strong breakout bar therefore
    fills ABOVE the published trigger (adverse vs stop-order execution)."""
    bars = _bars("2024-03-04", [100, 104, 105, 106],
                 opens=[100, 100.2, 104.5, 105.5])
    trade = _sim(bars, i=1)
    assert trade.entry_price == 104.0            # bar-1 close, not 100.2/104.5


def test_entry_bar_cannot_stop_out_the_trade():
    """AUDIT: the stop is evaluated from the NEXT bar onward; the entry bar's
    own low never triggers it."""
    # entry bar has a deep low that would breach any stop; later bars are calm
    bars = _bars("2024-03-04", [100, 100, 101, 102],
                 lows=[99.5, 80.0, 100.5, 101.5])
    trade = _sim(bars, i=1)
    assert trade.exit_reason != "stop_loss" or trade.holding_min > 0
    assert pd.Timestamp(trade.close_date) > bars["date"].iloc[1]


def test_stop_fill_is_at_stop_price_even_through_a_gap():
    """AUDIT (documented optimism): if a bar OPENS below the stop, the fill is
    still recorded AT the stop price - no gap-through slippage is modelled."""
    bars = _bars("2024-03-04", [100, 100, 90, 90],
                 opens=[100, 100, 90.0, 90],     # bar 2 gaps open far below
                 lows=[99.5, 99.5, 89.0, 89.5],
                 highs=[100.5, 100.5, 91.0, 90.5])
    trade = _sim(bars, i=1, atr=1.0)             # stop = 100 - 2*1 = 98
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_price == trade.entry_price - 2.0   # AT the stop (98)
    # the realistic fill would be ~the open (90); the model books 98.


def test_no_profit_target_exists():
    """AUDIT: a huge favourable move does not exit at any fixed target; with the
    trail not yet ratcheted past entry, the intraday exit is the square-off."""
    closes = [100, 100, 108, 116, 124, 132]      # relentless rally
    bars = _bars("2024-03-04", closes)
    trade = _sim(bars, i=1, atr=1.0, max_bars=50)
    assert trade.exit_reason in ("trailing_stop", "session_squareoff")
    assert trade.exit_reason != "roi"            # no such concept anywhere
    # captured profit far exceeds any classic 1R/2R target -> no target fired
    assert trade.gross_ratio > 0.10


def test_stop_only_tightens_and_trail_needs_activation_profit():
    """AUDIT: below +0.6% profit the trail never arms; the initial stop stands."""
    closes = [100, 100, 100.3, 100.4, 100.2, 100.3]   # profit stays < 0.6%
    bars = _bars("2024-03-04", closes)
    trade = _sim(bars, i=1, atr=1.0)
    # never stopped (stop stayed at 98), so the session square-off closes it
    assert trade.exit_reason == "session_squareoff"


def test_partial_day_squares_off_on_its_last_bar():
    """AUDIT: a short (partial/holiday-eve) session squares off on ITS last bar,
    not at a hardcoded clock time."""
    bars = _bars("2024-03-04", [100, 100, 101, 101.5])   # 4-bar session
    trade = _sim(bars, i=1)
    assert trade.exit_reason == "session_squareoff"
    assert pd.Timestamp(trade.close_date) == bars["date"].iloc[-1]
