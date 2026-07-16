"""NSE cost model: every charge checked against hand-computed values."""

import pytest

from algo.core.costs import NseCostParams, NseEquityCostModel, Product


def test_intraday_buy_charges_hand_computed():
    model = NseEquityCostModel()
    b = model.breakdown(price=1000.0, quantity=100, is_buy=True,
                        product=Product.INTRADAY)
    turnover = 100_000.0
    assert b["turnover"] == turnover
    assert b["brokerage"] == pytest.approx(20.0)      # 0.03% = 30 -> capped
    assert b["stt"] == 0.0                            # intraday: sell side only
    assert b["stamp"] == pytest.approx(3.0)           # 0.003% buy only
    assert b["exchange"] == pytest.approx(2.97)
    assert b["sebi"] == pytest.approx(0.10)
    assert b["gst"] == pytest.approx(0.18 * (20.0 + 2.97 + 0.10))
    assert b["slippage"] == pytest.approx(20.0)       # 2 bps
    assert b["total"] == pytest.approx(
        20.0 + 0.0 + 2.97 + 0.10 + 3.0 + b["gst"] + 20.0)


def test_intraday_sell_pays_stt_and_no_stamp():
    model = NseEquityCostModel()
    b = model.breakdown(price=1000.0, quantity=100, is_buy=False,
                        product=Product.INTRADAY)
    assert b["stt"] == pytest.approx(25.0)            # 0.025% of 100k
    assert b["stamp"] == 0.0                          # buy side only


def test_delivery_stt_both_sides_and_higher_stamp():
    model = NseEquityCostModel()
    buy = model.breakdown(price=1000.0, quantity=100, is_buy=True,
                          product=Product.DELIVERY)
    sell = model.breakdown(price=1000.0, quantity=100, is_buy=False,
                           product=Product.DELIVERY)
    assert buy["stt"] == pytest.approx(100.0)         # 0.1% both sides
    assert sell["stt"] == pytest.approx(100.0)
    assert buy["stamp"] == pytest.approx(15.0)        # 0.015% buy only
    assert sell["stamp"] == 0.0


def test_delivery_round_trip_costs_more_than_intraday():
    model = NseEquityCostModel()
    kw = dict(entry_price=1000.0, exit_price=1000.0, quantity=100)
    intraday = model.round_trip_pct(product=Product.INTRADAY, **kw)
    delivery = model.round_trip_pct(product=Product.DELIVERY, **kw)
    assert delivery > intraday                        # STT dominates delivery
    # sanity: both in a believable band for a 1 lakh ticket
    assert 0.0005 < intraday < 0.002
    assert 0.002 < delivery < 0.006


def test_every_component_is_configurable_no_broker_hardcoding():
    zero = NseCostParams(
        brokerage_pct=0.0, brokerage_cap=0.0, stt_intraday_sell=0.0,
        stt_delivery=0.0, exchange_txn_pct=0.0, sebi_pct=0.0,
        stamp_intraday_buy=0.0, stamp_delivery_buy=0.0, gst_pct=0.0,
        slippage_pct=0.0)
    model = NseEquityCostModel(zero)
    assert model.round_trip_pct(entry_price=100.0, exit_price=100.0,
                                quantity=10, product=Product.INTRADAY) == 0.0
    # a different broker's brokerage flows straight through
    flat = NseEquityCostModel(NseCostParams(brokerage_pct=0.005,
                                            brokerage_cap=1e9))
    b = flat.breakdown(price=1000.0, quantity=100, is_buy=True,
                       product=Product.INTRADAY)
    assert b["brokerage"] == pytest.approx(500.0)


def test_brokerage_cap_binds_only_above_threshold():
    model = NseEquityCostModel()
    small = model.breakdown(price=100.0, quantity=100, is_buy=True,
                            product=Product.INTRADAY)   # 10k turnover
    assert small["brokerage"] == pytest.approx(3.0)      # 0.03% < 20 cap
    big = model.breakdown(price=10_000.0, quantity=100, is_buy=True,
                          product=Product.INTRADAY)      # 1m turnover
    assert big["brokerage"] == pytest.approx(20.0)       # capped
