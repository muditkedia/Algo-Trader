"""STRAT-05 - 5-minute Gap Fill Failure Reversal."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import atr, session_vwap, slot_relative_volume
from algo.execution import ExecutionSpec
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import ConfidenceScore, clip01
from algo.strategies.opening_context import (
    add_opening_market_context, local_dates, shared_5m_features,
)


@dataclass(frozen=True)
class GapFillFailureParams:
    min_gap_pct: float = 0.008
    max_gap_pct: float = 0.030
    min_gap_penetration_pct: float = 0.25
    max_gap_penetration_pct: float = 0.75
    min_rvol_reversal: float = 1.80
    short_rvol_add: float = 0.40
    rvol_sessions: int = 10
    atr_period: int = 14
    stop_buffer_atr: float = 0.15
    risk_per_trade_pct: float = 0.01
    max_capital_per_trade: float = 0.20
    tp1_r: float = 1.50
    tp1_fraction: float = 0.50
    chandelier_atr: float = 2.00
    slippage_collar_pct: float = 0.001
    no_progress_bars: int = 8
    no_progress_r: float = 0.40
    vwap_touch_tolerance_pct: float = 0.0015

    @classmethod
    def from_dict(cls, data) -> "GapFillFailureParams":
        return from_dict(cls, data)


class GapFillFailure(StrategyProfile):

    meta = StrategyMeta(
        name="gap_fill_failure_5m", version="2.0.0", spec_id="STRAT-05",
        direction=Direction.BOTH, holding_scope=HoldingScope.INTRADAY,
        timeframe="5m", min_bars=1503,
        required_columns=(
            "close", "vwap", "atr", "rvol", "gap_pct", "gap_distance",
            "pivot_low", "pivot_high", "penetration_long",
            "penetration_short", "prior_close", "adt20", "nifty_trend",
            "bar_close_minute", "stop_long", "stop_short"),
        supported_regimes=("gap_continuation_reversal",),
        hypothesis=("A 25%-75% attempt to fill a meaningful opening gap that "
                    "holds above the prior close and reverses on renewed "
                    "same-slot volume exposes trapped counter-trend traders."),
        expected_behaviour=("At most one continuation entry in the original "
                            "gap direction between 09:25 and 11:00 IST."),
        known_failure_modes=(
            "broad-market liquidation that completes the gap fill",
            "low-volume pivots without institutional absorption",
            "late or repeated reversal attempts after 11:00 IST",
        ), enabled=True)

    execution = ExecutionSpec(
        entry="limit_collar", slippage_collar_pct=0.001,
        stop_kind="column", stop_long_col="stop_long",
        stop_short_col="stop_short", hard_stop_pct=None,
        target_kind="r", target_r=1.5, partial_fraction=0.5,
        trail="chandelier", trail_atr_mult=2.0, trail_after_partial=True,
        invalidation_long_col="invalidate_long",
        invalidation_short_col="invalidate_short",
        no_progress_bars=8, no_progress_r=0.4,
        risk_per_trade_pct=0.01, max_capital_per_trade=0.20,
        intraday=True, allow_overnight=False, max_hold_bars=None)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or GapFillFailureParams())

    @property
    def context_symbols(self) -> tuple:
        return ("NIFTY50",)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = shared_5m_features(dataframe, p.atr_period, p.rvol_sessions)
        local = local_dates(df)
        day = local.dt.normalize()
        df["opening_open"] = df["open"].groupby(day).transform("first")
        df["gap_pct"] = df["opening_open"] / df["prior_close"] - 1.0
        df["gap_distance"] = (df["opening_open"] - df["prior_close"]).abs()
        df["pivot_low"] = df["low"].groupby(day).cummin()
        df["pivot_high"] = df["high"].groupby(day).cummax()
        df["penetration_long"] = (
            (df["opening_open"] - df["pivot_low"])
            / df["gap_distance"].where(df["gap_distance"] != 0.0))
        df["penetration_short"] = (
            (df["pivot_high"] - df["opening_open"])
            / df["gap_distance"].where(df["gap_distance"] != 0.0))
        df["pivot_vwap_long"] = df["vwap"].where(
            df["low"] == df["pivot_low"]).groupby(day).ffill()
        df["pivot_vwap_short"] = df["vwap"].where(
            df["high"] == df["pivot_high"]).groupby(day).ffill()
        df["stop_long"] = df["pivot_low"] - p.stop_buffer_atr * df["atr"]
        df["stop_short"] = df["pivot_high"] + p.stop_buffer_atr * df["atr"]
        df["invalidate_long"] = df["close"] <= df["prior_close"]
        df["invalidate_short"] = df["close"] >= df["prior_close"]
        return df

    def prepare_context(self, frames: dict, context: dict) -> dict:
        return add_opening_market_context(frames, context)

    def _conditions(self, df: pd.DataFrame,
                    direction: Direction) -> pd.Series:
        p = self.settings
        is_long = direction == Direction.LONG
        gap = df["gap_pct"] if is_long else -df["gap_pct"]
        penetration = (df["penetration_long"] if is_long
                       else df["penetration_short"])
        pivot = df["pivot_low"] if is_long else df["pivot_high"]
        pivot_vwap = (df["pivot_vwap_long"] if is_long
                      else df["pivot_vwap_short"])
        no_fill = (pivot > df["prior_close"] + 0.10 * df["gap_distance"]
                   if is_long else
                   pivot < df["prior_close"] - 0.10 * df["gap_distance"])
        reversal = ((df["close"] > df["open"])
                    & (df["close"] > df["high"].shift(1)) if is_long else
                    (df["close"] < df["open"])
                    & (df["close"] < df["low"].shift(1)))
        vwap = df["close"] > df["vwap"] if is_long else df["close"] < df["vwap"]
        required_rvol = p.min_rvol_reversal + (0.0 if is_long
                                               else p.short_rvol_add)
        in_window = ((df["bar_close_minute"] >= 9 * 60 + 25)
                     & (df["bar_close_minute"] <= 11 * 60))
        vwap_touch = ((pivot - pivot_vwap).abs()
                      / pivot_vwap.where(pivot_vwap != 0.0)
                      <= p.vwap_touch_tolerance_pct)
        nifty = df["nifty_trend"] == (1.0 if is_long else -1.0)
        raw = ((gap >= p.min_gap_pct) & (gap <= p.max_gap_pct)
               & (penetration >= p.min_gap_penetration_pct)
               & (penetration <= p.max_gap_penetration_pct)
               & no_fill & reversal & vwap
               & (df["rvol"] >= required_rvol) & in_window
               & (df["adt20"] >= 500_000_000) & vwap_touch & nifty)
        session = local_dates(df).dt.normalize()
        return (raw & (raw.groupby(session).cumsum() == 1)).fillna(False)

    def entry_signals(self, dataframe: pd.DataFrame) -> dict:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            blank = self.no_signal(dataframe)
            return {Direction.LONG: blank, Direction.SHORT: blank.copy()}
        return {Direction.LONG: self._conditions(dataframe, Direction.LONG),
                Direction.SHORT: self._conditions(dataframe, Direction.SHORT)}

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        signals = self.entry_signals(dataframe)
        return signals[Direction.LONG] | signals[Direction.SHORT]

    def grade_multiplier(self, confidence: float) -> float:
        return 1.0

    def signal_priority(self, dataframe: pd.DataFrame, index: int,
                        direction: Direction, confidence: float,
                        regime: float) -> float:
        row = dataframe.iloc[index]
        penetration = float(row["penetration_long" if direction == Direction.LONG
                                else "penetration_short"])
        return 0.50 * float(row["rvol"]) + 0.50 / penetration

    def confidence_for(self, dataframe: pd.DataFrame,
                       direction: Direction) -> ConfidenceScore:
        required = {"rvol", "penetration_long", "penetration_short"}
        if dataframe.empty or not required.issubset(dataframe.columns):
            return ConfidenceScore.zero("unprepared frame")
        row = dataframe.iloc[-1]
        penetration = float(row["penetration_long" if direction == Direction.LONG
                                else "penetration_short"])
        volume = clip01(float(row["rvol"]) / 3.0)
        shallow = clip01(1.0 - penetration)
        return ConfidenceScore(
            score=0.50 * volume + 0.50 * shallow,
            components={
                "reversal_rvol": {"score": volume, "weight": 0.50,
                                  "detail": f"RVOL {row['rvol']:.2f}"},
                "gap_hold": {"score": shallow, "weight": 0.50,
                             "detail": f"penetration {penetration:.1%}"},
            }, reason=f"STRAT-05 {direction.value} gap-fill failure")

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if dataframe.empty or "gap_pct" not in dataframe.columns:
            return ConfidenceScore.zero("unprepared frame")
        direction = (Direction.LONG if dataframe["gap_pct"].iloc[-1] >= 0
                     else Direction.SHORT)
        return self.confidence_for(dataframe, direction)
