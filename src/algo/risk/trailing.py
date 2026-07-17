"""Trailing-stop modes - the promoted trail engine, made selectable.

The archived trailing engine (ATR chandelier + profit-lock tiers) stays exactly
as promoted - it was the crypto phase's ONLY profitable exit (v3: +553, 80% win
rate) and its behaviour is unchanged by default. This module adds three further
modes behind one interface, so a strategy may declare which trail suits its
thesis WITHOUT any strategy code change (it is a metadata field with an ATR
default; strategies that say nothing keep today's behaviour):

    atr         chandelier: current_price - k x ATR                (default)
    percentage  fixed give-back: current_price x (1 - pct)
    structure   under the recent swing low (structure-aware)
    volatility  ATR scaled by the volatility REGIME - trail wide when the
                stock is unusually volatile vs its own history, tight when calm

All modes return a CANDIDATE price; the caller ratchets (max of previous and
candidate), so stops remain monotonic by construction. Profit-lock tiers from
the promoted engine are applied by ``trail_stop_price`` for every mode, so the
tier ladder keeps working regardless of the chosen trail.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from algo.core.config import from_dict
from algo.risk.engine import RiskParams, locked_profit_for

ATR = "atr"
PERCENTAGE = "percentage"
STRUCTURE = "structure"
VOLATILITY = "volatility"
MODES = (ATR, PERCENTAGE, STRUCTURE, VOLATILITY)


@dataclass(frozen=True)
class TrailConfig:
    """Per-mode trailing parameters (configurable; ATR default = archive)."""

    mode: str = ATR
    #: activate the trail once profit reaches this fraction (archive default)
    activation_profit: float = 0.006
    # --- atr / volatility ---
    atr_multiplier: float = 2.0
    #: volatility mode: multiplier band scaled by the ATR%-vs-history ratio
    vol_min_multiplier: float = 1.5
    vol_max_multiplier: float = 3.5
    vol_lookback: int = 50
    # --- percentage ---
    percent: float = 0.02
    # --- structure ---
    structure_lookback: int = 10
    structure_buffer_pct: float = 0.001

    @classmethod
    def from_dict(cls, data) -> "TrailConfig":
        return from_dict(cls, data)


def _atr_candidate(current_price: float, atr: Optional[float],
                   config: TrailConfig) -> Optional[float]:
    if not atr or atr <= 0:
        return None
    return current_price - config.atr_multiplier * atr


def _percentage_candidate(current_price: float,
                          config: TrailConfig) -> Optional[float]:
    return current_price * (1.0 - config.percent)


def _structure_candidate(bars: Optional[pd.DataFrame],
                         config: TrailConfig) -> Optional[float]:
    if bars is None or bars.empty or "low" not in bars.columns:
        return None
    swing_low = float(bars["low"].tail(config.structure_lookback).min())
    if swing_low <= 0:
        return None
    return swing_low * (1.0 - config.structure_buffer_pct)


def _volatility_candidate(current_price: float, atr: Optional[float],
                          bars: Optional[pd.DataFrame],
                          config: TrailConfig) -> Optional[float]:
    """ATR trail whose multiplier adapts to the CURRENT volatility regime.

    ratio = current ATR% / median ATR% over the lookback. ratio > 1 (unusually
    volatile) widens the trail toward ``vol_max_multiplier``; calm tape tightens
    it toward ``vol_min_multiplier``. Falls back to the plain ATR trail when
    there is no history to compare against.
    """
    if not atr or atr <= 0 or current_price <= 0:
        return None
    multiplier = config.atr_multiplier
    if bars is not None and "atr" in bars.columns and len(bars) >= 5:
        atr_pct = (bars["atr"] / bars["close"]).tail(config.vol_lookback)
        median = float(atr_pct.median())
        current = atr / current_price
        if median > 0:
            ratio = current / median
            multiplier = min(config.vol_max_multiplier,
                             max(config.vol_min_multiplier,
                                 config.atr_multiplier * ratio))
    return current_price - multiplier * atr


def trail_stop_price(entry_price: float, current_price: float,
                     current_profit: float, atr: Optional[float],
                     config: TrailConfig, risk: RiskParams,
                     bars: Optional[pd.DataFrame] = None) -> Optional[float]:
    """Best trailing candidate for the configured mode, plus profit-lock tiers.

    Returns the HIGHEST applicable candidate (tightest protection), or None
    when nothing applies yet. Caller ratchets; never widens a stop.
    """
    candidates = []

    # profit-lock ladder (promoted engine - applies in every mode)
    locked = locked_profit_for(current_profit, risk)
    if locked is not None:
        candidates.append(entry_price * (1 + locked))

    if current_profit >= config.activation_profit:
        mode = config.mode if config.mode in MODES else ATR
        if mode == ATR:
            candidate = _atr_candidate(current_price, atr, config)
        elif mode == PERCENTAGE:
            candidate = _percentage_candidate(current_price, config)
        elif mode == STRUCTURE:
            candidate = _structure_candidate(bars, config)
        else:
            candidate = _volatility_candidate(current_price, atr, bars, config)
        if candidate is not None:
            candidates.append(candidate)

    return max(candidates) if candidates else None
