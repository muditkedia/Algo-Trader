"""Core primitives: config pattern, market config, calendar, cost model."""

from dataclasses import dataclass
from datetime import date, datetime

import pytest

from algo.core.config import MarketConfig, filter_fields, from_dict
from algo.core.calendar import StaticCalendar
from algo.core.costs import FlatCostModel, Product
from algo.core.logging import get_logger


@dataclass(frozen=True)
class _Sample:
    a: int = 1
    b: float = 2.0


def test_filter_fields_drops_unknown_keys():
    assert filter_fields(_Sample, {"a": 5, "zzz": 9}) == {"a": 5}
    assert filter_fields(_Sample, None) == {}


def test_from_dict_builds_and_ignores_noise():
    obj = from_dict(_Sample, {"a": 7, "unknown": 1})
    assert obj.a == 7 and obj.b == 2.0


def test_market_config_defaults_and_override():
    m = MarketConfig()
    assert m.name == "NSE" and m.trading_days_per_year == 252
    assert m.minutes_per_session == 375
    m2 = MarketConfig.from_dict({"trading_days_per_year": 250, "junk": 1})
    assert m2.trading_days_per_year == 250 and m2.currency == "INR"


def test_static_calendar_sessions_and_bounds():
    cal = StaticCalendar(["2024-01-01", "2024-01-02", "2024-01-05"])
    assert cal.is_session("2024-01-02")
    assert not cal.is_session("2024-01-03")
    assert cal.sessions("2024-01-01", "2024-01-04") == [
        date(2024, 1, 1), date(2024, 1, 2)]
    open_dt, close_dt = cal.session_bounds("2024-01-02")
    assert open_dt == datetime(2024, 1, 2, 9, 15)
    assert close_dt == datetime(2024, 1, 2, 15, 30)
    assert cal.session_bounds("2024-01-03") is None


def test_static_calendar_prev_next():
    cal = StaticCalendar(["2024-01-01", "2024-01-02", "2024-01-05"])
    assert cal.previous_session("2024-01-05") == date(2024, 1, 2)
    assert cal.next_session("2024-01-02") == date(2024, 1, 5)
    assert cal.next_session("2024-01-05") is None


def test_flat_cost_model_round_trip():
    model = FlatCostModel(per_side_pct=0.0005)  # 5 bps/side
    rt = model.round_trip_pct(entry_price=100.0, exit_price=100.0,
                              quantity=10, product=Product.INTRADAY)
    assert rt == pytest.approx(0.001)  # 10 bps round trip


def test_logger_namespaced():
    assert get_logger("evidence").name == "algo.evidence"
    assert get_logger("algo.already").name == "algo.already"
