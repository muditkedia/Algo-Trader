"""Dynamic position sizing - risk-based, confidence-scaled, budget-capped.

Replaces fixed staking. The core is the promoted risk engine's
``risk_based_stake`` (stake such that a full stop-out loses ``risk_per_trade``
of capital); this module scales that base by signal confidence and clamps it
against every configured limit:

    stake = risk_based_stake(capital, stop_pct)          # risk parity core
          x confidence multiplier (linear, configurable band)
    capped by: remaining daily risk budget / stop_pct    # risk budget
               remaining capital x max_capital_per_trade # concentration
               absolute min/max stake                    # hard limits

Volatility is already inside the core: the stop distance IS the ATR/structure
volatility measure, so higher-volatility names automatically size smaller.
Limits are never exceeded; if the honest maximum falls below ``min_stake``,
sizing returns 0 (skip) rather than forcing a position.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from algo.core.config import from_dict
from algo.risk.engine import RiskParams, risk_based_stake
from algo.strategies.confidence import clip01


@dataclass(frozen=True)
class SizingConfig:
    #: fraction of total capital risked per trade at confidence = 1.0
    risk_per_trade: float = 0.005
    #: confidence multiplier band: size scales linearly from ``low_mult`` (at
    #: confidence 0) to 1.0 (at confidence 1). 0.5 halves low-conviction sizes.
    confidence_low_mult: float = 0.5
    #: hard stake limits (account currency)
    min_stake: float = 10_000.0
    max_stake: float = 200_000.0
    #: no single position may exceed this fraction of the deployable pool
    max_capital_per_trade: float = 0.20
    # NOTE: the daily risk budget lives in PortfolioConfig (one owner). It
    # reaches sizing through the ``risk_budget_left`` argument, so there is no
    # second copy here to drift out of sync.

    @classmethod
    def from_dict(cls, data) -> "SizingConfig":
        return from_dict(cls, data)


def size_position(*, capital: float, available_capital: float,
                  stop_pct: Optional[float], confidence: float,
                  risk_budget_left: float,
                  config: SizingConfig,
                  pool: Optional[float] = None) -> float:
    """Stake for one new position; 0.0 means 'too small to take'.

    ``capital``  = account EQUITY. Risk parity is sized from equity because a
                   stop-out loses real equity - leverage never changes the loss,
                   only how much notional you may hold.
    ``pool``     = deployable BUYING POWER (equity for cash accounts, more when
                   the broker's configured intraday allowance applies). Used for
                   the concentration cap. Defaults to ``capital``.
    ``available_capital`` = what is left of the pool after open positions.
    """
    if not stop_pct or stop_pct <= 0 or capital <= 0 \
            or available_capital <= 0:
        return 0.0
    params = RiskParams(risk_per_trade=config.risk_per_trade)
    base = risk_based_stake(capital, stop_pct, None, None, params) or 0.0

    multiplier = (config.confidence_low_mult
                  + (1.0 - config.confidence_low_mult) * clip01(confidence))
    stake = base * multiplier

    # remaining daily risk budget: stake * stop_pct must fit inside it
    budget_cap = (max(0.0, risk_budget_left) / stop_pct
                  if stop_pct > 0 else 0.0)
    stake = min(stake,
                budget_cap,
                available_capital,
                (pool if pool else capital) * config.max_capital_per_trade,
                config.max_stake)
    if stake < config.min_stake:
        return 0.0
    return round(stake, 2)
