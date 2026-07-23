"""STRAT-08 - canonical 5-minute VWAP Trend Continuation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import atr, ema, session_vwap, slot_relative_volume
from algo.execution import ExecutionSpec
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import ConfidenceScore, clip01
from algo.strategies.opening_context import (
    completed_15m_adx, local_dates, shared_5m_features,
)


@dataclass(frozen=True)
class VwapTrendParams:
    min_vwap_slope_pct: float = 0.05
    vwap_touch_tolerance_pct: float = 0.0015
    max_vwap_penetration_atr: float = 0.40
    min_rvol_bounce: float = 1.50
    short_rvol_add: float = 0.30
    rvol_sessions: int = 10
    atr_period: int = 14
    stop_pivot_buffer_atr: float = 0.10
    stop_vwap_buffer_atr: float = 0.25
    risk_per_trade_pct: float = 0.01
    max_capital_per_trade: float = 0.20
    tp1_r: float = 1.50
    tp1_fraction: float = 0.50
    chandelier_atr: float = 2.00
    slippage_collar_pct: float = 0.001
    no_progress_bars: int = 6
    no_progress_r: float = 0.40
    min_adx15: float = 22.0
    min_rejection_wick_ratio: float = 0.35

    @classmethod
    def from_dict(cls, data) -> "VwapTrendParams":
        return from_dict(cls, data)


class VwapTrendContinuation(StrategyProfile):

    meta = StrategyMeta(
        name="vwap_trend_5m", version="2.0.0", spec_id="STRAT-08",
        blocked_by_pre_partial_groups=("ema_compression",),
        active_block_group="primary_trend",
        direction=Direction.BOTH, holding_scope=HoldingScope.INTRADAY,
        timeframe="5m", min_bars=1505,
        required_columns=(
            "close", "vwap", "vwap_slope_pct", "ema9", "ema20", "ema50",
            "rvol", "atr", "pivot_low", "pivot_high", "lower_wick_ratio",
            "upper_wick_ratio", "adx15", "adt20", "bar_close_minute",
            "stop_long", "stop_short"),
        supported_regimes=("intraday_trend",),
        hypothesis=("A volume-backed rejection of a steeply sloping VWAP "
                    "inside a strict EMA ribbon reveals renewed institutional "
                    "execution in the established intraday trend."),
        expected_behaviour=("Bidirectional continuation entries after 09:30 "
                            "when a shallow VWAP test resumes through the "
                            "prior candle."),
        known_failure_modes=(
            "flat VWAP chop with tangled moving averages",
            "deep penetrations that are trend failures, not pullbacks",
            "late-session bounces with insufficient time before square-off",
        ), enabled=True)

    execution = ExecutionSpec(
        entry="limit_collar", slippage_collar_pct=0.001,
        stop_kind="column", stop_long_col="stop_long",
        stop_short_col="stop_short", hard_stop_pct=None,
        target_kind="r", target_r=1.5, partial_fraction=0.5,
        trail="chandelier", trail_atr_mult=2.0, trail_after_partial=True,
        invalidation_long_col="invalidate_long",
        invalidation_short_col="invalidate_short",
        no_progress_bars=6, no_progress_r=0.4,
        risk_per_trade_pct=0.01, max_capital_per_trade=0.20,
        intraday=True, allow_overnight=False, max_hold_bars=None)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or VwapTrendParams())

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = shared_5m_features(dataframe, p.atr_period, p.rvol_sessions)
        local = local_dates(df)
        day = local.dt.normalize()
        df["vwap_slope_pct"] = df["vwap"].groupby(day).pct_change(3) * 100.0
        df["ema50"] = ema(df["close"], 50)
        df["pivot_low"] = (df["low"].groupby(day).rolling(
            3, min_periods=1).min().reset_index(level=0, drop=True))
        df["pivot_high"] = (df["high"].groupby(day).rolling(
            3, min_periods=1).max().reset_index(level=0, drop=True))
        candle_range = (df["high"] - df["low"]).where(
            df["high"] != df["low"])
        df["lower_wick_ratio"] = (
            np.minimum(df["open"], df["close"]) - df["low"]) / candle_range
        df["upper_wick_ratio"] = (
            df["high"] - np.maximum(df["open"], df["close"])) / candle_range
        adx15 = completed_15m_adx(df)
        if not adx15.empty:
            df = pd.merge_asof(df.sort_values("date"), adx15,
                               on="date", direction="backward")
        else:
            df["adx15"] = np.nan
        df["stop_long"] = np.minimum(
            df["pivot_low"] - p.stop_pivot_buffer_atr * df["atr"],
            df["vwap"] - p.stop_vwap_buffer_atr * df["atr"])
        df["stop_short"] = np.maximum(
            df["pivot_high"] + p.stop_pivot_buffer_atr * df["atr"],
            df["vwap"] + p.stop_vwap_buffer_atr * df["atr"])
        df["invalidate_long"] = df["close"] < df["vwap"]
        df["invalidate_short"] = df["close"] > df["vwap"]
        return df

    def _conditions(self, df: pd.DataFrame,
                    direction: Direction) -> pd.Series:
        p = self.settings
        is_long = direction == Direction.LONG
        ribbon = ((df["ema9"] > df["ema20"]) & (df["ema20"] > df["ema50"])
                  if is_long else
                  (df["ema9"] < df["ema20"]) & (df["ema20"] < df["ema50"]))
        slope = (df["vwap_slope_pct"] >= p.min_vwap_slope_pct if is_long
                 else df["vwap_slope_pct"] <= -p.min_vwap_slope_pct)
        touch = (df["low"] <= df["vwap"] * (1.0 + p.vwap_touch_tolerance_pct)
                 if is_long else
                 df["high"] >= df["vwap"] * (1.0 - p.vwap_touch_tolerance_pct))
        penetration = (df["close"] >= df["vwap"]
                       - p.max_vwap_penetration_atr * df["atr"] if is_long else
                       df["close"] <= df["vwap"]
                       + p.max_vwap_penetration_atr * df["atr"])
        reversal = ((df["close"] > df["open"])
                    & (df["close"] > df["vwap"])
                    & (df["close"] > df["high"].shift(1)) if is_long else
                    (df["close"] < df["open"])
                    & (df["close"] < df["vwap"])
                    & (df["close"] < df["low"].shift(1)))
        required_rvol = p.min_rvol_bounce + (0.0 if is_long
                                             else p.short_rvol_add)
        wick = df["lower_wick_ratio"] if is_long else df["upper_wick_ratio"]
        in_window = ((df["bar_close_minute"] >= 9 * 60 + 30)
                     & (df["bar_close_minute"] <= 14 * 60 + 45))
        return (ribbon & slope & touch & penetration & reversal
                & (df["rvol"] >= required_rvol) & in_window
                & (df["adt20"] >= 500_000_000) & (df["adx15"] >= p.min_adx15)
                & (wick >= p.min_rejection_wick_ratio)).fillna(False)

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
        return 0.50 * abs(float(row["vwap_slope_pct"])) + 0.50 * float(
            row["rvol"])

    def confidence_for(self, dataframe: pd.DataFrame,
                       direction: Direction) -> ConfidenceScore:
        required = {"vwap_slope_pct", "rvol"}
        if dataframe.empty or not required.issubset(dataframe.columns):
            return ConfidenceScore.zero("unprepared frame")
        row = dataframe.iloc[-1]
        slope_value = abs(float(row["vwap_slope_pct"]))
        slope = clip01(slope_value / 0.12)
        volume = clip01(float(row["rvol"]) / 2.5)
        return ConfidenceScore(
            score=0.50 * slope + 0.50 * volume,
            components={
                "vwap_slope": {"score": slope, "weight": 0.50,
                               "detail": f"slope {slope_value:.3f}%"},
                "bounce_rvol": {"score": volume, "weight": 0.50,
                                "detail": f"RVOL {row['rvol']:.2f}"},
            }, reason=f"STRAT-08 {direction.value} VWAP continuation")

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if dataframe.empty or "vwap_slope_pct" not in dataframe.columns:
            return ConfidenceScore.zero("unprepared frame")
        direction = (Direction.LONG
                     if dataframe["vwap_slope_pct"].iloc[-1] >= 0
                     else Direction.SHORT)
        return self.confidence_for(dataframe, direction)
