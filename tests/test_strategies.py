"""Strategy plugin interface and registry."""

import pandas as pd
import pytest

from algo.core.enums import Direction, HoldingScope
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.registry import StrategyRegistry


class DummyStrategy(StrategyProfile):
    meta = StrategyMeta(
        name="dummy", version="1.0", direction=Direction.LONG,
        holding_scope=HoldingScope.INTRADAY,
        required_columns=("ema_fast", "close"),
        supported_regimes=("bull",), hypothesis="toy", enabled=True)

    def entry_signal(self, dataframe):
        if self.missing_columns(dataframe):
            return self.no_signal(dataframe)
        return dataframe["close"] > dataframe["ema_fast"]


class DisabledStrategy(StrategyProfile):
    meta = StrategyMeta(
        name="disabled", version="0.1", direction=Direction.SHORT,
        holding_scope=HoldingScope.SWING, enabled=False)

    def entry_signal(self, dataframe):
        return self.no_signal(dataframe)


def test_profile_is_abstract():
    with pytest.raises(TypeError):
        StrategyProfile()  # abstract - entry_signal not implemented


def test_entry_signal_and_missing_columns():
    strat = DummyStrategy()
    good = pd.DataFrame({"close": [10, 11], "ema_fast": [9, 12]})
    result = strat.entry_signal(good)
    assert result.tolist() == [True, False]
    # missing required column -> safe empty signal, never raises
    bad = pd.DataFrame({"close": [10, 11]})
    assert strat.missing_columns(bad) == ["ema_fast"]
    assert strat.entry_signal(bad).tolist() == [False, False]


def test_registry_register_and_create():
    reg = StrategyRegistry()
    reg.register(DummyStrategy)
    assert "dummy" in reg and reg.enabled_names() == ["dummy"]
    strat = reg.create("dummy")
    assert isinstance(strat, DummyStrategy)
    assert "dummy" in reg.describe_all()


def test_registry_rejects_duplicate_name():
    reg = StrategyRegistry()
    reg.register(DummyStrategy)
    reg.register(DummyStrategy)  # same class again is fine

    class OtherDummy(DummyStrategy):
        pass
    with pytest.raises(ValueError):
        reg.register(OtherDummy)  # same meta.name, different class


def test_registry_refuses_disabled_by_default():
    reg = StrategyRegistry()
    reg.register(DisabledStrategy)
    assert reg.enabled_names() == []
    with pytest.raises(ValueError):
        reg.create("disabled")
    assert isinstance(reg.create("disabled", require_enabled=False),
                      DisabledStrategy)


def test_discover_missing_package_is_empty():
    reg = StrategyRegistry()
    assert reg.discover("algo.strategies._nonexistent_plugins") == []
