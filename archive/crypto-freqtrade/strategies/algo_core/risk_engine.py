"""Risk engine - stop-loss sizing, hard-stop enforcement, and position sizing.

Rules (see architecture/ARCHITECTURE.md):

* Initial stop = the wider of (ATR multiple) and (distance below the recent
  swing low), expressed as a fraction of price. Structure-aware, never
  arbitrary.
* Hard emergency stop: if the required initial stop exceeds
  ``hard_stop_pct`` (6%), the trade is NOT tradeable and must be rejected.
* Progressive profit locking: as profit crosses each tier threshold the stop
  is raised to lock in the tier's floor. Stops only ever tighten.
* Risk-based stake sizing: risk a fixed fraction of the wallet per trade,
  derived from the stop distance. Gated by RiskParams.enable_risk_sizing.

All functions are pure so they can be unit-tested without Freqtrade. All
parameters come from ``algo_core.settings.RiskParams`` - the single source of
truth for risk thresholds.
"""

from __future__ import annotations

from typing import Optional

from algo_core.settings import RiskParams


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


def locked_profit_for(current_profit: float, params: RiskParams) -> Optional[float]:
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
    """Stake sized so a full stop-out loses ``risk_per_trade`` of the wallet.

    Returns None when the inputs required to size by risk are unavailable, so
    the caller can fall back to the proposed stake. The result is clamped to
    [min_stake, max_stake] - crucially NOT to the proposed stake, so risk
    sizing can legitimately exceed a fixed configured stake. This requires the
    config to use ``"stake_amount": "unlimited"``; otherwise Freqtrade caps the
    stake upstream and sizing cannot take effect.
    """
    if not available or not stop_pct or stop_pct <= 0:
        return None
    stake = available * params.risk_per_trade / stop_pct
    if max_stake:
        stake = min(stake, max_stake)
    if min_stake:
        stake = max(stake, min_stake)
    return stake
