"""Interactive session setup (§4).

The operator sets the session's capital at startup instead of editing a config
file first. The decisive property: the choices are held IN MEMORY for the run
and the file on disk is never rewritten, so today's numbers cannot silently
become tomorrow's defaults.

The dialogue takes its reader/writer by injection, so every path is exercised
here without a terminal.
"""

import pytest

from algo.trading.config import TradingConfig
from algo.trading.session_setup import (
    SessionChoices, ask_number, ask_percent, ask_yes_no, broker_cash,
    run_wizard, warnings_for,
)


class Script:
    """Feeds scripted answers and records what was printed."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []
        self.output = []

    def read(self, prompt):
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else ""

    def write(self, text=""):
        self.output.append(str(text))

    @property
    def text(self):
        return "\n".join(self.output)


def _config(**kw):
    capital = {"deploy_today": 300_000, "max_daily_loss": 10_000,
               "max_open_positions": 5, "min_trade_allocation": 0.25}
    capital.update(kw.pop("capital", {}))
    return TradingConfig.from_dict({"capital": capital, **kw})


# ================================================================== prompts

def test_empty_input_keeps_the_default():
    s = Script("")
    assert ask_number("Deploy", 300_000, reader=s.read, writer=s.write) == 300_000


def test_a_bad_number_is_re_asked_not_accepted():
    """This dialogue sets how much money may be deployed; a typo must not
    silently become the session's limit."""
    s = Script("abc", "-5", "250000")
    value = ask_number("Deploy", 300_000, minimum=1.0, reader=s.read,
                       writer=s.write)
    assert value == 250_000
    assert len(s.prompts) == 3
    assert "not a number" in s.text and "at least" in s.text


def test_numbers_may_carry_thousands_separators():
    s = Script("2,50,000")
    assert ask_number("Deploy", 300_000, reader=s.read, writer=s.write) == 250_000


def test_a_maximum_is_enforced():
    s = Script("400000", "90000")
    value = ask_number("Loss", 10_000, maximum=100_000, reader=s.read,
                       writer=s.write)
    assert value == 90_000 and "must not exceed" in s.text


def test_integers_reject_fractions():
    s = Script("4.5", "4")
    assert ask_number("Positions", 5, integer=True, reader=s.read,
                      writer=s.write) == 4
    assert "whole number" in s.text


@pytest.mark.parametrize("typed, expected", [
    ("25", 0.25), ("0.25", 0.25), ("25%", 0.25), ("50", 0.5), ("", 0.25),
])
def test_percentages_accept_either_form(typed, expected):
    s = Script(typed)
    assert ask_percent("Min", 0.25, s.read, s.write) == pytest.approx(expected)


def test_out_of_range_percentages_are_re_asked():
    s = Script("150", "30")
    assert ask_percent("Min", 0.25, s.read, s.write) == pytest.approx(0.30)
    assert "between 0% and 100%" in s.text


@pytest.mark.parametrize("typed, default, expected", [
    ("y", True, True), ("n", True, False), ("", True, True),
    ("", False, False), ("YES", False, True),
])
def test_yes_no(typed, default, expected):
    s = Script(typed)
    assert ask_yes_no("Continue?", default, s.read, s.write) is expected


# =================================================================== wizard

def test_paper_wizard_collects_every_value():
    s = Script("500000", "20000", "4", "50", "y")
    choices = run_wizard(_config(), reader=s.read, writer=s.write)
    assert choices.deploy_today == 500_000
    assert choices.max_daily_loss == 20_000
    assert choices.max_open_positions == 4
    assert choices.min_trade_allocation == pytest.approx(0.5)
    assert "Trading Session" in s.text


def test_declining_the_confirmation_starts_nothing():
    s = Script("500000", "20000", "4", "25", "n")
    assert run_wizard(_config(), reader=s.read, writer=s.write) is None
    assert "cancelled" in s.text


def test_the_summary_is_shown_before_the_confirmation():
    s = Script("", "", "", "", "y")
    run_wizard(_config(), reader=s.read, writer=s.write)
    summary_at = s.text.index("Deploy Capital Today")
    confirm_at = next(i for i, p in enumerate(s.prompts) if "Continue?" in p)
    assert summary_at >= 0 and confirm_at == len(s.prompts) - 1


# ============================================================ session-only

def test_choices_never_touch_the_config_on_disk(tmp_path):
    """The whole reason the wizard exists: no permanent state is overwritten."""
    import json
    path = tmp_path / "cfg.json"
    original = {"capital": {"deploy_today": 300_000, "max_daily_loss": 10_000}}
    path.write_text(json.dumps(original), encoding="utf-8")

    config = TradingConfig.from_dict(json.loads(path.read_text()))
    choices = SessionChoices(mode="paper", deploy_today=999_000,
                             max_daily_loss=50_000, max_open_positions=2,
                             min_trade_allocation=0.4)
    session = choices.apply(config)

    assert session.risk.deploy_today == 999_000        # the run sees the new
    assert config.risk.deploy_today == 300_000         # the object is untouched
    assert json.loads(path.read_text()) == original    # the FILE is untouched


def test_apply_preserves_every_unrelated_setting():
    config = _config(store_dir="somewhere/else", symbols_file="x.txt")
    session = SessionChoices(mode="paper", deploy_today=1, max_daily_loss=1,
                             max_open_positions=1,
                             min_trade_allocation=0.25).apply(config)
    assert session.store_dir == "somewhere/else"
    assert session.symbols_file == "x.txt"


# ======================================================= live: broker cash

class CashAdapter:
    def __init__(self, value):
        self.value = value

    def available_cash(self):
        return self.value


class BrokenAdapter:
    def available_cash(self):
        raise RuntimeError("broker unreachable")


def test_live_offers_the_brokers_cash():
    s = Script("y", "20000", "5", "25", "y")
    config = _config(mode="live")
    choices = run_wizard(config, adapter=CashAdapter(487_500.0),
                         reader=s.read, writer=s.write)
    assert choices.broker_cash == 487_500.0
    assert choices.used_broker_cash and choices.deploy_today == 487_500.0
    assert "Available Broker Cash" in s.text


def test_declining_broker_cash_lets_the_operator_choose():
    s = Script("n", "100000", "5000", "3", "25", "y")
    choices = run_wizard(_config(mode="live"), adapter=CashAdapter(487_500.0),
                         reader=s.read, writer=s.write)
    assert not choices.used_broker_cash
    assert choices.deploy_today == 100_000


def test_deploying_more_than_the_broker_holds_is_refused():
    s = Script("n", "900000", "100000", "5000", "3", "25", "y")
    choices = run_wizard(_config(mode="live"), adapter=CashAdapter(487_500.0),
                         reader=s.read, writer=s.write)
    assert choices.deploy_today == 100_000
    assert "must not exceed" in s.text


def test_an_unreadable_balance_asks_rather_than_inventing_one():
    """A fabricated balance offered as the account's is worse than asking."""
    assert broker_cash(BrokenAdapter()) is None
    s = Script("250000", "10000", "5", "25", "y")
    choices = run_wizard(_config(mode="live"), adapter=BrokenAdapter(),
                         reader=s.read, writer=s.write)
    assert choices.broker_cash is None
    assert choices.deploy_today == 250_000
    assert "could not be read" in s.text


def test_dict_shaped_balances_are_understood():
    class DictAdapter:
        def available_cash(self):
            return {"availablecash": "123456.78"}
    assert broker_cash(DictAdapter()) == pytest.approx(123_456.78)


# ================================================================ warnings

def test_the_allocation_floor_warns_about_unreachable_position_counts():
    """A 25% floor allows at most 4 positions, so max_positions=5 cannot be
    reached - not obvious from either number alone."""
    choices = SessionChoices(mode="paper", deploy_today=300_000,
                             max_daily_loss=10_000, max_open_positions=5,
                             min_trade_allocation=0.25)
    text = " ".join(warnings_for(choices))
    assert "at most 4 concurrent positions" in text


def test_an_impossible_floor_is_flagged():
    choices = SessionChoices(mode="paper", deploy_today=300_000,
                             max_daily_loss=10_000, max_open_positions=2,
                             min_trade_allocation=0.9)
    assert any("no trade could be sized" in w for w in warnings_for(choices))


def test_a_non_binding_loss_limit_is_flagged():
    choices = SessionChoices(mode="paper", deploy_today=100_000,
                             max_daily_loss=100_000, max_open_positions=2,
                             min_trade_allocation=0.25)
    assert any("cannot bind" in w for w in warnings_for(choices))


def test_warnings_appear_before_the_operator_confirms():
    s = Script("300000", "10000", "5", "25", "y")
    run_wizard(_config(), reader=s.read, writer=s.write)
    warn_at = s.text.index("concurrent positions")
    assert warn_at < len(s.text)
    assert "Continue?" in s.prompts[-1]
