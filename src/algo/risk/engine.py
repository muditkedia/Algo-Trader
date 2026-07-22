"""Risk engine - PROMOTED VERBATIM from the archived crypto risk_engine.py (git history).

The archive README flagged this module as a promotion candidate: its functions
are pure (no Freqtrade, no market assumptions), so they are reused as-is rather
than rewritten. The crypto phase's own evidence supports keeping them: the
trailing-stop mechanism was the ONLY profitable exit it ever produced (v3:
+553 USDT, 80% win rate), and the risk framework was sound even when the edge
was not (L-001).

Rules (unchanged):
* Initial stop = the wider of (ATR multiple) and (distance below the recent
  swing low), as a fraction of price - beyond both noise and structure.
* Hard stop: a trade needing a stop wider than ``hard_stop_pct`` is rejected.
* Progressive profit locking: as profit crosses each tier the stop is raised.
  Stops only ever tighten.
* Risk-based sizing derived from the stop distance.

Only change from the archive: ``RiskParams`` drops ``exit_adx_floor`` /
``exit_rsi_floor`` - those parameterized the crypto STRUCTURAL exits, which
D-006 removed after all three timeframes produced a 0% win rate. Units are
account currency (INR here); the maths is unit-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from algo.core.config import from_dict


@dataclass(frozen=True)
class RiskParams:
    """Stop-loss, hard-cap, profit-locking, and sizing parameters."""

    #: Absolute maximum loss per trade. Trades needing a wider stop are rejected.
    hard_stop_pct: float = 0.06
    #: Initial stop distance as a multiple of ATR.
    atr_stop_multiplier: float = 2.0
    #: Trailing (chandelier) stop distance as a multiple of ATR.
    trail_atr_multiplier: float = 2.0
    #: Profit ratio at which the ATR trail activates.
    trail_activation_profit: float = 0.006
    #: Expected reward as a multiple of ATR (for the risk/reward check).
    reward_atr_multiple: float = 3.0
    #: Minimum acceptable reward/risk ratio.
    min_risk_reward: float = 1.3
    #: Fraction of capital risked per trade (only when sizing is enabled).
    risk_per_trade: float = 0.01
    #: Progressive profit locking: (profit threshold, locked profit floor).
    profit_lock_tiers: tuple = (
        (0.010, 0.001),
        (0.018, 0.008),
        (0.028, 0.015),
        (0.045, 0.028),
    )

    @classmethod
    def from_dict(cls, data) -> "RiskParams":
        params = from_dict(cls, data)
        if data and "profit_lock_tiers" in data:
            tiers = tuple(tuple(t) for t in data["profit_lock_tiers"])
            params = RiskParams(**{**params.__dict__,
                                   "profit_lock_tiers": tiers})
        return params


def initial_stop_pct(
    close: Optional[float],
    atr: Optional[float],
    swing_low: Optional[float],
    params: RiskParams,
) -> Optional[float]:
    """Initial stop distance as a fraction of price, or None if not computable.

    Uses the wider of the ATR-based distance and the distance below the recent
    swing low, so the stop sits beyond both noise and structure.
    """
    if not close or not atr or close <= 0 or atr <= 0:
        return None
    distance = params.atr_stop_multiplier * atr
    if swing_low and 0 < swing_low < close:
        distance = max(distance, close - swing_low)
    return distance / close


def stop_is_tradeable(stop_pct: Optional[float], params: RiskParams) -> bool:
    """A trade is only tradeable if its required stop fits inside the hard stop."""
    return stop_pct is not None and 0 < stop_pct <= params.hard_stop_pct


def locked_profit_for(current_profit: float,
                      params: RiskParams) -> Optional[float]:
    """Highest profit floor unlocked by ``current_profit``, or None below tier 1."""
    locked = None
    for threshold, floor in params.profit_lock_tiers:
        if current_profit >= threshold:
            locked = floor
    return locked


def trailing_stop_price(
    open_rate: float,
    current_rate: float,
    current_profit: float,
    atr: Optional[float],
    params: RiskParams,
) -> Optional[float]:
    """Candidate stop price from profit locking and the ATR trail.

    Returns the highest applicable candidate, or None when neither applies.
    The caller is responsible for ratcheting (never lowering an existing stop).
    """
    candidates = []
    locked = locked_profit_for(current_profit, params)
    if locked is not None:
        candidates.append(open_rate * (1 + locked))
    if atr and atr > 0 and current_profit >= params.trail_activation_profit:
        candidates.append(current_rate - params.trail_atr_multiplier * atr)
    return max(candidates) if candidates else None


def risk_based_stake(
    available: Optional[float],
    stop_pct: Optional[float],
    min_stake: Optional[float],
    max_stake: Optional[float],
    params: RiskParams,
) -> Optional[float]:
    """Stake sized so a full stop-out loses ``risk_per_trade`` of capital."""
    if not available or not stop_pct or stop_pct <= 0:
        return None
    stake = available * params.risk_per_trade / stop_pct
    if max_stake:
        stake = min(stake, max_stake)
    if min_stake:
        stake = max(stake, min_stake)
    return stake
