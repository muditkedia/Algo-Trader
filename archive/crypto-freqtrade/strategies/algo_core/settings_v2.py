"""algo_core.settings_v2 - AdaptiveTrend v2 parameters (evidence-derived, Phases D/E).

v2 keeps v1's IndicatorParams / EngineParams / RiskParams for the PRESERVED
components and layers on v2-specific values for the REDESIGNED ones: tighter
trailing (capture), an anti-chase entry filter, and a cost gate. Every default
encodes a Phase D/E finding and is documented in docs/DECISIONS.md. Overridable
via config['algo_trader_v2']; v1 is unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from algo_core.settings import AlgoSettings, RiskParams, _filter


@dataclass(frozen=True)
class V2Params:
    # --- anti-chase (Phase E: chasing extended moves is harmful; AUC ~0.45) ---
    #: reject entry when close sits more than this fraction above the 5m fast EMA
    max_ext_above_fast_5m: float = 0.003
    #: reject entry when the prior-1h return already exceeds this (chasing a pop)
    max_mom_1h: float = 0.015

    # --- cost awareness (Phase E: entry edge ~17bps < 20bps round-trip cost;
    #     observed MFE ~2x the 15m ATR%). Reward is estimated from that OBSERVED
    #     ratio, NOT a fixed per-trade ATR multiple. ---
    round_trip_cost: float = 0.002
    min_reward_cost_multiple: float = 2.0
    empirical_mfe_per_atr: float = 2.0

    # --- tighter trailing (Phase D: v1 captured only 43% of MFE, gave back
    #     1.26% per trade). Tighter trail + earlier activation + higher locks. ---
    trail_atr_multiplier: float = 1.25          # v1: 2.0
    trail_activation_profit: float = 0.004       # v1: 0.006
    profit_lock_tiers: tuple = (                  # v1 locked ~10% of each tier
        (0.008, 0.004),
        (0.015, 0.010),
        (0.025, 0.018),
        (0.040, 0.030),
    )

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "V2Params":
        kwargs = _filter(cls, data)
        if "profit_lock_tiers" in kwargs:
            kwargs["profit_lock_tiers"] = tuple(
                tuple(t) for t in kwargs["profit_lock_tiers"])
        return cls(**kwargs)


@dataclass(frozen=True)
class V2Settings:
    """Bundles the preserved v1 settings with the v2 overlay."""

    base: AlgoSettings
    v2: V2Params

    @classmethod
    def from_config(cls, config: Optional[dict]) -> "V2Settings":
        base = AlgoSettings.from_config(config)
        section = (config or {}).get("algo_trader_v2", {}) or {}
        return cls(base=base, v2=V2Params.from_dict(section))

    def risk_params(self) -> RiskParams:
        """v1 risk framework (hard stop + sizing preserved) with v2 tighter trail.

        Only the trailing/locking fields change; hard_stop_pct,
        atr_stop_multiplier, reward/min_risk_reward, and risk_per_trade are
        inherited unchanged, so the hard stop and position-sizing framework are
        identical to v1.
        """
        return replace(
            self.base.risk,
            trail_atr_multiplier=self.v2.trail_atr_multiplier,
            trail_activation_profit=self.v2.trail_activation_profit,
            profit_lock_tiers=self.v2.profit_lock_tiers,
        )
