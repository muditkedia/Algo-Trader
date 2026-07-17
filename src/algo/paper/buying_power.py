"""Buying power - what the portfolio may actually deploy.

Position sizing must work from real buying power, not from an assumed cash
balance and NEVER from a hardcoded leverage figure. Three configurable modes:

    cash        buying power == the configured trading capital (no leverage).
    multiplier  capital x ``intraday_multiplier`` - an explicitly CONFIGURED
                broker allowance (the value is config, not a constant in code).
    broker      read it from the broker: SmartAPI ``rmsLimit`` funds, using the
                configured field preference; falls back to ``fallback_mode`` if
                the account/endpoint does not report it.

Intraday and delivery differ (delivery is cash-like; intraday carries the
broker's margin allowance), so callers pass the product. Nothing here assumes
5x - or any x - unless the owner configures it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from algo.core.config import from_dict
from algo.core.costs import Product
from algo.core.logging import get_logger

logger = get_logger("paper.buying_power")


@dataclass(frozen=True)
class BuyingPowerConfig:
    #: cash | multiplier | broker
    mode: str = "cash"
    #: used by mode="multiplier" (and as the broker-mode sanity ceiling).
    #: 1.0 = no leverage. The OWNER sets this to their broker's allowance.
    intraday_multiplier: float = 1.0
    #: delivery (CNC) is cash-like for most retail accounts; configurable.
    delivery_multiplier: float = 1.0
    #: mode="broker": RMS fields to try, in order, for intraday buying power.
    broker_intraday_fields: Sequence[str] = (
        "availableintradaypayin", "availablecash", "net")
    #: mode="broker": RMS fields for delivery buying power.
    broker_delivery_fields: Sequence[str] = ("availablecash", "net")
    #: mode="broker" fallback when the broker reports nothing usable.
    fallback_mode: str = "cash"
    #: never allow buying power beyond capital x this, whatever the broker says
    #: (a guard against a mis-parsed field silently 100x-ing risk).
    max_multiplier: float = 10.0

    @classmethod
    def from_dict(cls, data) -> "BuyingPowerConfig":
        return from_dict(cls, data)


def _numeric(value) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None      # reject NaN


def from_rms(rms: dict, fields: Sequence[str]) -> Optional[float]:
    """First positive numeric value among ``fields`` in an RMS payload."""
    if not isinstance(rms, dict):
        return None
    lowered = {str(k).lower(): v for k, v in rms.items()}
    for name in fields:
        value = _numeric(lowered.get(name.lower()))
        if value is not None and value > 0:
            return value
    return None


def resolve_buying_power(capital: float, config: BuyingPowerConfig,
                         product: Product = Product.INTRADAY,
                         rms: Optional[dict] = None) -> float:
    """Deployable buying power for ``product``. Never raises; always explains."""
    multiplier = (config.intraday_multiplier if product == Product.INTRADAY
                  else config.delivery_multiplier)
    ceiling = capital * config.max_multiplier

    if config.mode == "broker":
        fields = (config.broker_intraday_fields if product == Product.INTRADAY
                  else config.broker_delivery_fields)
        reported = from_rms(rms or {}, fields)
        if reported is not None:
            power = min(reported, ceiling)
            if power < reported:
                logger.warning("broker reported buying power %.0f above the "
                               "configured ceiling %.0f - clamped",
                               reported, ceiling)
            logger.info("buying power from broker RMS: %.0f (%s)",
                        power, product.value)
            return power
        logger.warning("broker RMS reported no usable buying-power field %s - "
                       "falling back to mode=%r", list(fields),
                       config.fallback_mode)
        mode = config.fallback_mode
    else:
        mode = config.mode

    if mode == "multiplier":
        return min(capital * multiplier, ceiling)
    return capital                                   # cash
