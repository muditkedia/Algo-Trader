"""Interactive session setup - the operator's start-of-day dialogue.

Editing a config file before every session is both tedious and dangerous: the
edit persists, so yesterday's capital silently becomes today's default and a
number typed for one experiment stays in force for weeks. This asks for the
session's parameters at startup instead and holds them IN MEMORY ONLY. Nothing
here writes to disk; ``TradingConfig`` is rebuilt for the run and the file on
disk is never touched.

Paper mode asks for every figure. Live mode reads the broker's available cash
first and offers it, because the one number the operator should not be
retyping from memory is how much money the account actually holds.

Non-interactive runs (``--no-wizard``, a pipe, CI) skip the dialogue entirely
and use the configured values, so automation is unaffected.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, Optional

from algo.core.logging import get_logger
from algo.trading.config import TradingConfig

logger = get_logger("trading.session_setup")

WIDTH = 56


@dataclass
class SessionChoices:
    """What the operator chose for THIS session (never persisted)."""

    mode: str
    deploy_today: float
    max_daily_loss: float
    max_open_positions: int
    min_trade_allocation: float
    broker_cash: Optional[float] = None
    used_broker_cash: bool = False

    def summary_rows(self):
        rows = [
            ("Mode", self.mode.upper()),
            ("Deploy Capital Today", f"Rs {self.deploy_today:,.0f}"),
            ("Maximum Daily Loss", f"Rs {self.max_daily_loss:,.0f}"),
            ("Maximum Positions", str(self.max_open_positions)),
            ("Minimum Trade Allocation",
             f"{self.min_trade_allocation:.0%} "
             f"(Rs {self.deploy_today * self.min_trade_allocation:,.0f})"),
        ]
        if self.broker_cash is not None:
            rows.insert(1, ("Available Broker Cash",
                            f"Rs {self.broker_cash:,.0f}"))
        return rows

    def apply(self, config: TradingConfig) -> TradingConfig:
        """A NEW config carrying these choices. The original is untouched and
        nothing is written to disk - the session owns these values, the file
        does not."""
        return TradingConfig.from_dict({
            **config.to_dict(),
            "mode": self.mode,
            "capital": {
                "deploy_today": self.deploy_today,
                "max_daily_loss": self.max_daily_loss,
                "max_open_positions": self.max_open_positions,
                "min_trade_allocation": self.min_trade_allocation,
            },
        })


# ------------------------------------------------------------------- prompts

def _rule(char: str = "=") -> str:
    return char * WIDTH


def ask_number(prompt: str, default: float, *, minimum: float = 0.0,
               maximum: Optional[float] = None, integer: bool = False,
               reader: Callable[[str], str] = input,
               writer: Callable[[str], None] = print) -> float:
    """Prompt until a valid number is entered. Empty input keeps the default.

    Re-asks rather than accepting a bad value: this dialogue sets how much real
    money may be deployed, so a typo must not silently become the session's
    limit.
    """
    shown = f"{default:,.0f}" if not integer else f"{default:,d}"
    while True:
        raw = reader(f"  {prompt} [{shown}]: ").strip().replace(",", "")
        if not raw:
            return int(default) if integer else float(default)
        try:
            value = float(raw)
        except ValueError:
            writer(f"    not a number: {raw!r}")
            continue
        if value < minimum:
            writer(f"    must be at least {minimum:,.0f}")
            continue
        if maximum is not None and value > maximum:
            writer(f"    must not exceed {maximum:,.0f}")
            continue
        if integer and value != int(value):
            writer("    must be a whole number")
            continue
        return int(value) if integer else value


def ask_percent(prompt: str, default: float,
                reader: Callable[[str], str] = input,
                writer: Callable[[str], None] = print) -> float:
    """A percentage entered as 25 or 0.25; returned as a fraction."""
    while True:
        raw = reader(f"  {prompt} [{default:.0%}]: ").strip().rstrip("%")
        if not raw:
            return float(default)
        try:
            value = float(raw)
        except ValueError:
            writer(f"    not a number: {raw!r}")
            continue
        fraction = value / 100.0 if value > 1.0 else value
        if not 0.0 <= fraction <= 1.0:
            writer("    must be between 0% and 100%")
            continue
        return fraction


def ask_yes_no(prompt: str, default: bool = True,
               reader: Callable[[str], str] = input,
               writer: Callable[[str], None] = print) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        raw = reader(f"  {prompt} ({hint}): ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        writer("    please answer y or n")


# -------------------------------------------------------------------- wizard

def broker_cash(adapter) -> Optional[float]:
    """Available cash from the broker, or None if it cannot be read.

    Never guesses: an unavailable balance leads to the operator being asked,
    not to a fabricated number being offered as the account's.
    """
    for attr in ("available_cash", "funds", "margin"):
        fn = getattr(adapter, attr, None)
        if not callable(fn):
            continue
        try:
            value = fn()
        except Exception as exc:
            logger.warning("could not read broker cash via %s(): %s", attr, exc)
            return None
        if isinstance(value, dict):
            for key in ("availablecash", "available_cash", "net", "cash"):
                if key in value:
                    value = value[key]
                    break
        try:
            cash = float(value)
        except (TypeError, ValueError):
            continue
        if cash >= 0:
            return cash
    return None


def run_wizard(config: TradingConfig, adapter=None, *,
               reader: Callable[[str], str] = input,
               writer: Callable[[str], None] = print
               ) -> Optional[SessionChoices]:
    """Ask for the session's parameters. Returns None if the operator declines.

    ``reader``/``writer`` are injected so the whole dialogue is testable
    without a terminal.
    """
    L = config.risk
    writer("")
    writer(_rule())
    writer(" Trading Session")
    writer(_rule())

    cash = None
    used_cash = False
    deploy_default = L.deploy_today

    if config.is_live and adapter is not None:
        cash = broker_cash(adapter)
        if cash is None:
            writer("  Available Broker Cash        could not be read")
        else:
            writer(f"  Available Broker Cash        Rs {cash:,.0f}")
            if ask_yes_no("Use available funds?", True, reader, writer):
                deploy_default = cash
                used_cash = True

    writer("")
    if used_cash:
        writer(f"  Deploy Capital Today         Rs {deploy_default:,.0f} "
               f"(broker cash)")
        deploy = deploy_default
    else:
        deploy = ask_number("Deploy Capital Today", deploy_default,
                            minimum=1.0, maximum=cash, reader=reader,
                            writer=writer)
    max_loss = ask_number("Maximum Daily Loss", L.max_daily_loss, minimum=1.0,
                          maximum=deploy, reader=reader, writer=writer)
    max_pos = int(ask_number("Maximum Positions", L.max_open_positions,
                             minimum=1, maximum=100, integer=True,
                             reader=reader, writer=writer))
    allocation = ask_percent("Minimum Trade Allocation",
                             L.min_trade_allocation, reader, writer)

    choices = SessionChoices(
        mode=config.mode, deploy_today=deploy, max_daily_loss=max_loss,
        max_open_positions=max_pos, min_trade_allocation=allocation,
        broker_cash=cash, used_broker_cash=used_cash)

    writer("")
    writer(_rule())
    for label, value in choices.summary_rows():
        writer(f" {label:<28} {value}")
    for warning in warnings_for(choices):
        writer(f" ! {warning}")
    writer(_rule())
    if not ask_yes_no("Continue?", True, reader, writer):
        writer("  cancelled - no session started")
        return None
    writer("")
    return choices


def warnings_for(choices: SessionChoices) -> list:
    """Consequences of the chosen numbers that are not obvious from them.

    Shown BEFORE the confirmation, because they change what the session can
    actually do and the operator is about to approve it.
    """
    out = []
    if choices.min_trade_allocation > 0:
        concurrent = int(1.0 / choices.min_trade_allocation)
        if concurrent < choices.max_open_positions:
            out.append(
                f"a {choices.min_trade_allocation:.0%} minimum allocation "
                f"allows at most {concurrent} concurrent positions, so "
                f"Maximum Positions = {choices.max_open_positions} cannot be "
                f"reached")
        floor = choices.deploy_today * choices.min_trade_allocation
        cap = choices.deploy_today * 0.5
        if floor > cap:
            out.append(
                f"the minimum allocation (Rs {floor:,.0f}) exceeds the "
                f"per-trade cap (Rs {cap:,.0f}) - no trade could be sized")
    if choices.max_daily_loss >= choices.deploy_today:
        out.append("the daily loss limit is not smaller than the capital "
                   "deployed - it cannot bind")
    return out


def interactive_available(stream=None) -> bool:
    """True when there is a real terminal to hold the dialogue on."""
    stream = stream or sys.stdin
    try:
        return bool(stream is not None and stream.isatty())
    except Exception:
        return False
