"""algo_core.settings - the single source of truth for every tunable value.

All thresholds, indicator periods, and risk parameters live here as frozen
dataclasses. Nothing else in algo_core hardcodes a threshold: modules receive
an ``AlgoSettings`` (or one of its sub-params objects) and read from it. This
guarantees exactly one definition per threshold and makes every value
overridable from ``config.json`` under the ``algo_trader`` key, e.g.:

    "algo_trader": {
        "active_profile": "trend_following",
        "indicators": { "base_ema_fast": 9 },
        "engine":     { "min_score": 0.6, "adx_min_1h": 22 },
        "risk":       { "enable_risk_sizing": false, "risk_per_trade": 0.01 }
    }

Only keys that match a dataclass field are applied; unknown keys are ignored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields
from typing import Optional

#: Minutes per timeframe, used to size warmup (startup_candle_count).
TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


def _filter(cls, data: Optional[dict]) -> dict:
    """Keep only dict keys that correspond to fields of ``cls``."""
    valid = {f.name for f in fields(cls)}
    return {k: v for k, v in (data or {}).items() if k in valid}


@dataclass(frozen=True)
class IndicatorParams:
    """Indicator periods for the base (5m) and informative timeframes."""

    base_ema_fast: int = 9
    base_ema_slow: int = 21
    ema_15m_fast: int = 20
    ema_15m_slow: int = 50
    ema_1h_fast: int = 21
    ema_1h_slow: int = 50
    ema_4h_fast: int = 21
    ema_4h_slow: int = 50
    adx_period: int = 14
    rsi_period: int = 14
    atr_period: int = 14
    volume_window: int = 20
    structure_window: int = 10
    trend_shift: int = 6

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "IndicatorParams":
        return cls(**_filter(cls, data))

    @property
    def max_lookback(self) -> int:
        """Largest per-timeframe warmup (in candles) any indicator needs.

        ADX and the structure comparison need roughly double their period,
        so they are counted as ``2 * period`` here.
        """
        return max(
            self.base_ema_slow,
            self.ema_15m_slow,
            self.ema_1h_slow,
            self.ema_4h_slow,
            2 * self.adx_period,
            self.rsi_period,
            self.atr_period,
            self.volume_window,
            2 * self.structure_window,
            self.trend_shift,
        )


@dataclass(frozen=True)
class EngineParams:
    """GO / NO-GO thresholds.

    Mandatory-gate thresholds (trend, liquidity) and advisory-score
    thresholds (RSI, volume, volatility) plus the conviction gate.
    """

    # --- mandatory: trend ---
    adx_min_1h: float = 22.0
    adx_min_15m: float = 20.0
    # --- mandatory: liquidity ---
    min_quote_volume: float = 50_000.0
    max_spread_pct: float = 0.0015
    # --- advisory: momentum (RSI) ---
    rsi_min: float = 50.0
    rsi_max: float = 75.0
    rsi_ideal: float = 60.0
    # --- advisory: volume ---
    volume_ratio_min: float = 1.2
    # --- advisory: volatility ---
    atr_pct_min: float = 0.0010
    atr_pct_max: float = 0.025
    # --- conviction gate (advisory checks only) ---
    min_score: float = 0.60
    weight_rsi: float = 0.40
    weight_volume: float = 0.35
    weight_volatility: float = 0.25

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "EngineParams":
        return cls(**_filter(cls, data))


@dataclass(frozen=True)
class RiskParams:
    """Stop-loss, hard-cap, profit-locking, exit, and sizing parameters."""

    #: Absolute maximum loss per trade. Trades needing a wider stop are rejected.
    hard_stop_pct: float = 0.06
    #: Initial stop distance as a multiple of the 15m ATR.
    atr_stop_multiplier: float = 2.0
    #: Trailing (chandelier) stop distance as a multiple of the 15m ATR.
    trail_atr_multiplier: float = 2.0
    #: Profit ratio at which the ATR trail activates.
    trail_activation_profit: float = 0.006
    #: Expected reward as a multiple of ATR (for the risk/reward check).
    reward_atr_multiple: float = 3.0
    #: Minimum acceptable reward/risk ratio.
    min_risk_reward: float = 1.3
    #: Fraction of the wallet risked per trade (only used when sizing is enabled).
    risk_per_trade: float = 0.01
    #: Master switch for risk-based position sizing. When False (default) the
    #: strategy uses the fixed config stake_amount. Enable ONLY together with
    #: config "stake_amount": "unlimited" (see custom_stake_amount).
    enable_risk_sizing: bool = False
    #: Objective exit thresholds (15m). Exits fire on trend failure, never time.
    exit_adx_floor: float = 18.0
    exit_rsi_floor: float = 40.0
    #: Progressive profit locking: (profit threshold, locked profit floor),
    #: both price ratios relative to the open rate.
    profit_lock_tiers: tuple = (
        (0.010, 0.001),
        (0.018, 0.008),
        (0.028, 0.015),
        (0.045, 0.028),
    )

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "RiskParams":
        kwargs = _filter(cls, data)
        if "profit_lock_tiers" in kwargs:
            kwargs["profit_lock_tiers"] = tuple(
                tuple(tier) for tier in kwargs["profit_lock_tiers"]
            )
        return cls(**kwargs)


@dataclass(frozen=True)
class AlgoSettings:
    """Top-level settings bundle assembled from config['algo_trader']."""

    active_profile: str = "trend_following"
    indicators: IndicatorParams = field(default_factory=IndicatorParams)
    engine: EngineParams = field(default_factory=EngineParams)
    risk: RiskParams = field(default_factory=RiskParams)

    @classmethod
    def from_config(cls, config: Optional[dict]) -> "AlgoSettings":
        section = (config or {}).get("algo_trader", {}) or {}
        return cls(
            active_profile=section.get("active_profile", "trend_following"),
            indicators=IndicatorParams.from_dict(section.get("indicators")),
            engine=EngineParams.from_dict(section.get("engine")),
            risk=RiskParams.from_dict(section.get("risk")),
        )

    def startup_candles(
        self, base_timeframe: str, informative_timeframes: tuple, safety: float = 1.5
    ) -> int:
        """Base-timeframe candles needed to fully warm up every indicator.

        The binding constraint is the highest informative timeframe's slowest
        indicator (e.g. the 4h EMA50). We convert that lookback into base
        candles and apply a safety multiple so EMAs are well converged, not
        merely non-NaN. Freqtrade fetches each informative timeframe at its own
        resolution, so this large base count maps to a modest informative count.
        """
        base_minutes = TIMEFRAME_MINUTES[base_timeframe]
        needed = self.indicators.base_ema_slow
        for tf in informative_timeframes:
            ratio = TIMEFRAME_MINUTES[tf] / base_minutes
            needed = max(needed, self.indicators.max_lookback * ratio)
        return int(math.ceil(needed * safety))
