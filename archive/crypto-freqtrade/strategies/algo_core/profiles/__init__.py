"""Profile registry - single source of truth for available trading styles.

Exactly one profile is active at a time (config key
``algo_trader.active_profile``, default ``trend_following``). Scaffolded
profiles are registered but cannot be activated until their ``enabled``
flag is set and their signals are implemented.
"""

from __future__ import annotations

from algo_core.profiles.base import StrategyProfile
from algo_core.profiles.breakout import BreakoutProfile
from algo_core.profiles.mean_reversion import MeanReversionProfile
from algo_core.profiles.pullback_trend import PullbackTrendProfile
from algo_core.profiles.trend_following import TrendFollowingProfile
from algo_core.profiles.volatility_expansion import VolatilityExpansionProfile

DEFAULT_PROFILE = "trend_following"

_PROFILE_CLASSES = {
    cls.name: cls
    for cls in (
        TrendFollowingProfile,
        PullbackTrendProfile,
        BreakoutProfile,
        MeanReversionProfile,
        VolatilityExpansionProfile,
    )
}


def available_profiles() -> dict:
    """Mapping of profile name -> enabled flag."""
    return {name: cls.enabled for name, cls in _PROFILE_CLASSES.items()}


def get_active_profile(name: str = DEFAULT_PROFILE, **kwargs) -> StrategyProfile:
    """Instantiate the requested profile; refuse unknown or disabled ones."""
    cls = _PROFILE_CLASSES.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown profile '{name}'. Available: {sorted(_PROFILE_CLASSES)}"
        )
    if not cls.enabled:
        raise ValueError(
            f"Profile '{name}' is scaffolded but not enabled. "
            "Implement its signals and set enabled=True before activation."
        )
    return cls(**kwargs)
