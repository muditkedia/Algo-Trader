"""Typed row models for the evidence database.

Plain dataclasses (no ORM) mirroring the writable tables. The Evidence Logger
turns these into inserts. Controlled vocabularies are ``str`` enums so they
serialize as their string value directly.

Every ``Signal`` field except the few required ones defaults to ``None`` - the
platform records whatever context exists at signal time and never blocks a
recording because a field is missing (the "record everything" principle).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Re-exported for convenience so callers can import vocabularies alongside the
# row models; the definitions live in core.enums (single source).
from algo.core.enums import (  # noqa: F401
    Direction, Disposition, Mode, StrategyStatus,
)


@dataclass
class Run:
    """A processing run - the lineage every evidence row traces back to."""
    kind: str                       # research | scan | labeler | calibration | backtest
    started_at: str                 # ISO-8601 UTC
    finished_at: Optional[str] = None
    git_commit: Optional[str] = None
    config_hash: Optional[str] = None
    data_window: Optional[str] = None   # JSON string
    notes: Optional[str] = None
    run_id: Optional[int] = None        # filled after insert


@dataclass
class StrategyRecord:
    name: str
    version: str
    params_hash: Optional[str] = None
    status: str = StrategyStatus.DRAFT.value
    status_reason: Optional[str] = None
    status_at: Optional[str] = None
    created_at: Optional[str] = None
    strategy_id: Optional[int] = None


@dataclass
class Signal:
    """One candidate signal - recorded whatever its disposition."""
    # --- required identity ---
    ts: str
    symbol: str
    strategy_id: int
    direction: str
    mode: str
    disposition: str
    run_id: Optional[int] = None
    # --- market snapshot ---
    entry_price: Optional[float] = None
    spread_pct: Optional[float] = None
    atr_pct: Optional[float] = None
    relative_volume: Optional[float] = None
    traded_value: Optional[float] = None
    dist_from_ema: Optional[float] = None
    day_return_so_far: Optional[float] = None
    gap_open_pct: Optional[float] = None
    # --- context labels ---
    trend_regime: Optional[str] = None
    vol_regime: Optional[str] = None
    market_regime: Optional[str] = None
    sector: Optional[str] = None
    sector_return_5d: Optional[float] = None
    breadth_pct_above_ema50: Optional[float] = None
    # --- decision ---
    confidence_score: Optional[float] = None
    confidence_components: Optional[dict] = None   # JSON-encoded on write
    expected_reward_pct: Optional[float] = None
    expected_risk_pct: Optional[float] = None
    expected_holding_min: Optional[float] = None
    expected_value_net: Optional[float] = None
    # --- disposition detail ---
    disposition_reason: Optional[str] = None
    rank_in_scan: Optional[int] = None
    # filled after insert
    signal_id: Optional[int] = None


@dataclass
class SignalOutcome:
    """Matured forward outcome for a signal (written by the labeler)."""
    signal_id: int
    labeled_at: Optional[str] = None
    ret_15m: Optional[float] = None
    ret_30m: Optional[float] = None
    ret_60m: Optional[float] = None
    ret_120m: Optional[float] = None
    ret_eod: Optional[float] = None
    ret_1d: Optional[float] = None
    ret_3d: Optional[float] = None
    ret_5d: Optional[float] = None
    mfe_pct: Optional[float] = None
    mae_pct: Optional[float] = None
    mfe_time_min: Optional[float] = None
    mae_time_min: Optional[float] = None
    overnight_gap_pct: Optional[float] = None
    cost_model_version: Optional[str] = None
    est_cost_pct: Optional[float] = None
    sim_exit_price: Optional[float] = None
    sim_exit_reason: Optional[str] = None
    sim_holding_min: Optional[float] = None
    sim_pnl_net: Optional[float] = None
    exit_quality: Optional[float] = None


@dataclass
class TradeRecord:
    """An executed/simulated trade in the canonical trade schema (+ linkage).

    Column names match the validation package's canonical trades frame so the
    whole battery consumes this table unchanged (``symbol`` maps to that frame's
    ``pair`` at load time).
    """
    mode: str
    symbol: str
    open_date: str
    close_date: str
    profit_ratio: Optional[float] = None
    profit_abs: Optional[float] = None
    stake_amount: Optional[float] = None
    trade_duration: Optional[float] = None
    exit_reason: Optional[str] = None
    enter_tag: Optional[str] = None
    stop_distance_pct: Optional[float] = None
    signal_id: Optional[int] = None
    strategy_id: Optional[int] = None
    trade_id: Optional[int] = None
