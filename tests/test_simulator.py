"""Trade simulator: each exit path exercised on hand-built bars."""

import numpy as np
import pandas as pd
import pytest

from algo.core.costs import NseCostParams, NseEquityCostModel, Product
from algo.research.simulator import simulate_trade
from algo.risk.engine import RiskParams

FREE = NseEquityCostModel(NseCostParams(
    brokerage_pct=0.0, brokerage_cap=0.0, stt_intraday_sell=0.0,
    stt_delivery=0.0, exchange_txn_pct=0.0, sebi_pct=0.0,
    stamp_intraday_buy=0.0, stamp_delivery_buy=0.0, gst_pct=0.0,
    slippage_pct=0.0))


def _bars(closes, highs=None, lows=None, start="2024-03-04 09:15",
          freq="15min", days=None):
    n = len(closes)
    dates = (pd.DatetimeIndex(days) if days is not None
             else pd.date_range(start, periods=n, freq=freq, tz="UTC"))
    return pd.DataFrame({
        "date": dates,
        "open": closes, "high": highs if highs is not None else closes,
        "low": lows if lows is not None else closes, "close": closes,
        "volume": np.full(n, 100.0), "symbol": "TEST"})


def test_stop_loss_exit():
    # entry 100, ATR 1 -> 2 ATR stop = 98; bar 2 trades down to 97
    closes = [100.0, 99.5, 97.5, 99.0]
    lows = [100.0, 99.0, 97.0, 98.5]
    trade = simulate_trade(_bars(closes, lows=lows), 0, atr=1.0, swing_low=None,
                           params=RiskParams(), cost_model=FREE,
                           product=Product.INTRADAY)
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_price == pytest.approx(98.0)
    assert trade.profit_ratio == pytest.approx(-0.02)
    assert trade.stop_distance_pct == pytest.approx(0.02)


def test_trailing_stop_exit_after_ratchet():
    # rally locks profit tiers, then a pullback hits the RAISED stop
    closes = [100.0, 103.0, 105.0, 101.0]
    lows = [100.0, 102.5, 104.0, 100.0]
    highs = [100.0, 103.5, 105.5, 104.0]
    trade = simulate_trade(_bars(closes, highs=highs, lows=lows), 0, atr=1.0,
                           swing_low=None, params=RiskParams(),
                           cost_model=FREE, product=Product.INTRADAY)
    assert trade.exit_reason == "trailing_stop"
    assert trade.exit_price > 100.0          # exited in profit, not at 98
    assert trade.profit_ratio > 0


def test_session_squareoff_for_intraday():
    # flat drift, no stop hit -> must close at the session's last bar
    closes = [100.0, 100.2, 100.1, 100.3]
    trade = simulate_trade(_bars(closes), 0, atr=1.0, swing_low=None,
                           params=RiskParams(), cost_model=FREE,
                           product=Product.INTRADAY)
    assert trade.exit_reason == "session_squareoff"
    assert trade.close_date.normalize() == trade.open_date.normalize()
    assert trade.exit_price == pytest.approx(100.3)


def test_delivery_holds_across_sessions():
    days = pd.to_datetime(["2024-03-04", "2024-03-05", "2024-03-06",
                           "2024-03-07"]).tz_localize("UTC")
    closes = [100.0, 100.2, 100.1, 100.4]
    trade = simulate_trade(_bars(closes, days=days), 0, atr=1.0, swing_low=None,
                           params=RiskParams(), cost_model=FREE,
                           product=Product.DELIVERY)
    assert trade.exit_reason == "horizon_end"          # no square-off for CNC
    assert trade.close_date.normalize() > trade.open_date.normalize()


def test_stop_uses_wider_of_atr_and_structure():
    # swing low 95 is wider than the 2-ATR (98) stop -> 5% stop
    closes = [100.0, 99.0, 94.0, 99.0]
    lows = [100.0, 98.5, 93.5, 98.0]
    trade = simulate_trade(_bars(closes, lows=lows), 0, atr=1.0, swing_low=95.0,
                           params=RiskParams(), cost_model=FREE,
                           product=Product.INTRADAY)
    assert trade.stop_distance_pct == pytest.approx(0.05)
    assert trade.exit_price == pytest.approx(95.0)


def test_hard_stop_caps_stop_distance():
    # swing low 80 would imply a 20% stop -> capped at the 6% hard stop
    closes = [100.0, 99.0, 90.0, 99.0]
    lows = [100.0, 98.5, 89.0, 98.0]
    trade = simulate_trade(_bars(closes, lows=lows), 0, atr=1.0, swing_low=80.0,
                           params=RiskParams(), cost_model=FREE,
                           product=Product.INTRADAY)
    assert trade.stop_distance_pct == pytest.approx(0.06)


def test_costs_reduce_net_pnl_and_are_recorded():
    closes = [100.0, 100.2, 100.1, 100.3]
    priced = simulate_trade(_bars(closes), 0, atr=1.0, swing_low=None,
                            params=RiskParams(), cost_model=NseEquityCostModel(),
                            product=Product.INTRADAY)
    assert priced.cost_ratio > 0
    assert priced.profit_ratio == pytest.approx(
        priced.gross_ratio - priced.cost_ratio)
    assert priced.profit_ratio < priced.gross_ratio


def test_canonical_record_output():
    closes = [100.0, 100.2, 100.1, 100.3]
    trade = simulate_trade(_bars(closes), 0, atr=1.0, swing_low=None,
                           params=RiskParams(), cost_model=FREE,
                           product=Product.INTRADAY)
    record = trade.to_record(strategy_id=1, enter_tag="x")
    assert record.symbol == "TEST" and record.strategy_id == 1
    assert record.profit_ratio == trade.profit_ratio
    assert record.trade_duration == trade.holding_min
    assert record.exit_reason == "session_squareoff"
