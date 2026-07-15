"""Configuration pattern - the single-source-of-truth mechanism, generalized.

Reuses the crypto project's proven settings design (frozen dataclasses + a
field-filtering ``from_dict`` + config overrides; see docs/DECISIONS.md D-001)
but as market-agnostic helpers rather than a fixed strategy schema. Every tunable
lives in exactly one frozen dataclass; unknown config keys are ignored.

Provides:
    filter_fields(cls, data)   keep only dict keys that are fields of ``cls``.
    from_dict(cls, data)       build a dataclass from a (possibly noisy) dict.
    MarketConfig               market-level constants (NSE defaults, all
                               overridable) - the home for values like the
                               252-day year and session bounds.

Strategy-level parameter bundles are defined by each strategy when strategies
exist (Phase 3); the platform foundation only needs the mechanism + the market
constants.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Optional, Type, TypeVar

T = TypeVar("T")


def filter_fields(cls: Type, data: Optional[dict]) -> dict:
    """Keep only dict keys that correspond to fields of dataclass ``cls``."""
    valid = {f.name for f in fields(cls)}
    return {k: v for k, v in (data or {}).items() if k in valid}


def from_dict(cls: Type[T], data: Optional[dict]) -> T:
    """Construct a dataclass ``cls`` from ``data``, ignoring unknown keys."""
    return cls(**filter_fields(cls, data))


@dataclass(frozen=True)
class MarketConfig:
    """Market-level constants. Defaults describe NSE cash equities but every
    field is overridable, so the platform is not hard-wired to one venue.

    ``trading_days_per_year`` and the session bounds are consumed by the
    research/reporting layers when equity data flows (Phase 2); they live here
    so there is exactly one definition of each.
    """

    name: str = "NSE"
    timezone: str = "Asia/Kolkata"
    currency: str = "INR"
    trading_days_per_year: int = 252
    session_open: str = "09:15"       # HH:MM local (timezone above)
    session_close: str = "15:30"
    minutes_per_session: int = 375     # 09:15 -> 15:30

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "MarketConfig":
        return from_dict(cls, data)
