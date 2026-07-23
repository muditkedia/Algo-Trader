"""STRAT-10 - canonical 5-minute Geometric Channel Continuation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import atr, session_vwap, slot_relative_volume
from algo.execution import ExecutionSpec
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import ConfidenceScore, clip01
from algo.strategies.opening_context import (
    add_opening_market_context, completed_15m_channel_slope, local_dates,
    session_linear_channel, shared_5m_features,
)


@dataclass(frozen=True)
class GeometricChannelParams:
    channel_lookback_bars: int = 20
    min_r2_score: float = 0.70
    min_channel_slope_pct: float = 0.0004
    channel_stddev_mult: float = 2.00
    channel_touch_tol_pct: float = 0.0015
    max_channel_breach_atr: float = 0.40
    min_rvol_bounce: float = 1.50
    short_rvol_add: float = 0.50
    rvol_sessions: int = 10
    atr_period: int = 14
    channel_stop_buffer_atr: float = 0.15
    pivot_stop_buffer_atr: float = 0.10
    risk_per_trade_pct: float = 0.01
    max_capital_per_trade: float = 0.20
    tp1_r: float = 1.50
    tp1_fraction: float = 0.50
    tp2_fraction: float = 0.25
    chandelier_atr: float = 2.00
    slippage_collar_pct: float = 0.001
    no_progress_bars: int = 8
    no_progress_r: float = 0.40
    use_15m_slope: bool = True
    use_channel_width: bool = True
    use_nifty_slope: bool = True

    @classmethod
    def from_dict(cls, data) -> "GeometricChannelParams":
        return from_dict(cls, data)


class GeometricChannelContinuation(StrategyProfile):

    meta = StrategyMeta(
        name="geometric_channel_5m", version="2.0.0", spec_id="STRAT-10",
        blocked_by_active_groups=("primary_trend",),
        direction=Direction.BOTH, holding_scope=HoldingScope.INTRADAY,
        timeframe="5m", min_bars=1501,
        required_columns=(
            "close", "channel_mid", "channel_slope_pct", "channel_r2",
            "channel_upper", "channel_lower", "channel_width", "vwap",
            "rvol", "atr", "pivot_low", "pivot_high", "adt20",
            "bar_close_minute", "channel_slope_15m",
            "nifty_channel_slope_pct", "stop_long", "stop_short"),
        supported_regimes=("structured_trend",),
        hypothesis=("A statistically linear prior intraday channel reveals "
                    "systematic stepped order flow; a shallow boundary test "
                    "and high-RVOL median reclaim can resume that flow."),
        expected_behaviour=("Bidirectional continuation from a projected "
                            "prior-only 20-bar regression envelope."),
        known_failure_modes=(
            "low-R2 nonlinear price action",
            "flat channels without sufficient slope",
            "deep boundary breaches that invalidate the structure",
        ), enabled=True)

    execution = ExecutionSpec(
        entry="limit_collar", slippage_collar_pct=0.001,
        stop_kind="column", stop_long_col="stop_long",
        stop_short_col="stop_short", hard_stop_pct=None,
        target_kind="r", target_r=1.5, partial_fraction=0.5,
        target2_long_col="channel_upper", target2_short_col="channel_lower",
        target2_partial_fraction=0.25, dynamic_target2=True,
        trail="chandelier", trail_atr_mult=2.0, trail_after_partial=True,
        invalidation_long_col="invalidate_long",
        invalidation_short_col="invalidate_short",
        no_progress_bars=8, no_progress_r=0.4,
        risk_per_trade_pct=0.01, max_capital_per_trade=0.20,
        intraday=True, allow_overnight=False, max_hold_bars=None)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or GeometricChannelParams())

    @property
    def context_symbols(self) -> tuple:
        return ("NIFTY50",)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = shared_5m_features(dataframe, p.atr_period, p.rvol_sessions)
        local = local_dates(df)
        day = local.dt.normalize()
        channel = session_linear_channel(
            df, p.channel_lookback_bars, p.channel_stddev_mult)
        df["channel_mid"] = channel["baseline"]
        df["channel_slope_pct"] = channel["slope_pct"]
        df["channel_r2"] = channel["r2"]
        df["channel_resid_std"] = channel["resid_std"]
        df["channel_upper"] = channel["upper"]
        df["channel_lower"] = channel["lower"]
        df["channel_width"] = df["channel_upper"] - df["channel_lower"]
        df["pivot_low"] = df["low"].groupby(day).transform(
            lambda values: values.rolling(3, min_periods=1).min())
        df["pivot_high"] = df["high"].groupby(day).transform(
            lambda values: values.rolling(3, min_periods=1).max())
        df["stop_long"] = np.minimum(
            df["channel_lower"] - p.channel_stop_buffer_atr * df["atr"],
            df["pivot_low"] - p.pivot_stop_buffer_atr * df["atr"])
        df["stop_short"] = np.maximum(
            df["channel_upper"] + p.channel_stop_buffer_atr * df["atr"],
            df["pivot_high"] + p.pivot_stop_buffer_atr * df["atr"])
        df["invalidate_long"] = ((df["close"] < df["channel_lower"])
                                 | (df["close"] < df["vwap"]))
        df["invalidate_short"] = ((df["close"] > df["channel_upper"])
                                  | (df["close"] > df["vwap"]))
        return df

    def prepare_context(self, frames: dict, context: dict) -> dict:
        frames = add_opening_market_context(frames, context)
        for df in frames.values():
            slope15 = completed_15m_channel_slope(
                df, self.settings.channel_lookback_bars)
            if slope15.empty:
                df["channel_slope_15m"] = np.nan
                continue
            merged = pd.merge_asof(
                df.sort_values("date"), slope15, on="date",
                direction="backward")
            df.drop(columns=list(df.columns), inplace=True)
            for col in merged.columns:
                df[col] = merged[col].to_numpy()
        return frames

    def _conditions(self, df: pd.DataFrame,
                    direction: Direction) -> pd.Series:
        p = self.settings
        is_long = direction == Direction.LONG
        slope = (df["channel_slope_pct"] >= p.min_channel_slope_pct
                 if is_long else
                 df["channel_slope_pct"] <= -p.min_channel_slope_pct)
        touch = (df["low"] <= df["channel_lower"]
                 * (1.0 + p.channel_touch_tol_pct) if is_long else
                 df["high"] >= df["channel_upper"]
                 * (1.0 - p.channel_touch_tol_pct))
        breach = (df["close"] >= df["channel_lower"]
                  - p.max_channel_breach_atr * df["atr"] if is_long else
                  df["close"] <= df["channel_upper"]
                  + p.max_channel_breach_atr * df["atr"])
        rebound = ((df["close"] > df["open"])
                   & (df["close"] > df["channel_mid"])
                   & (df["close"] > df["high"].shift(1)) if is_long else
                   (df["close"] < df["open"])
                   & (df["close"] < df["channel_mid"])
                   & (df["close"] < df["low"].shift(1)))
        vwap = df["close"] > df["vwap"] if is_long \
            else df["close"] < df["vwap"]
        required_rvol = p.min_rvol_bounce + (0.0 if is_long
                                             else p.short_rvol_add)
        slope15 = (df["channel_slope_15m"] > 0 if is_long
                   else df["channel_slope_15m"] < 0)
        nifty = (df["nifty_channel_slope_pct"] > 0 if is_long
                 else df["nifty_channel_slope_pct"] < 0)
        in_window = ((df["bar_close_minute"] >= 9 * 60 + 35)
                     & (df["bar_close_minute"] <= 14 * 60 + 45))
        conditions = ((df["channel_r2"] >= p.min_r2_score) & slope & touch
                      & breach & rebound & vwap
                      & (df["rvol"] >= required_rvol) & in_window
                      & (df["adt20"] >= 500_000_000))
        if p.use_15m_slope:
            conditions &= slope15
        if p.use_channel_width:
            conditions &= df["channel_width"] >= df["atr"]
        if p.use_nifty_slope:
            conditions &= nifty
        return conditions.fillna(False)

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
        return 0.50 * float(row["channel_r2"]) + 0.50 * abs(float(
            row["channel_slope_pct"]))

    def confidence_for(self, dataframe: pd.DataFrame,
                       direction: Direction) -> ConfidenceScore:
        required = {"channel_r2", "channel_slope_pct"}
        if dataframe.empty or not required.issubset(dataframe.columns):
            return ConfidenceScore.zero("unprepared frame")
        row = dataframe.iloc[-1]
        fit = clip01(float(row["channel_r2"]))
        slope_value = abs(float(row["channel_slope_pct"]))
        slope = clip01(slope_value / 0.0010)
        return ConfidenceScore(
            score=0.50 * fit + 0.50 * slope,
            components={
                "channel_fit": {"score": fit, "weight": 0.50,
                                "detail": f"R² {row['channel_r2']:.3f}"},
                "channel_slope": {
                    "score": slope, "weight": 0.50,
                    "detail": f"slope {slope_value:.4%}/bar"},
            }, reason=f"STRAT-10 {direction.value} channel continuation")

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if dataframe.empty or "channel_slope_pct" not in dataframe.columns:
            return ConfidenceScore.zero("unprepared frame")
        direction = (Direction.LONG if dataframe["channel_slope_pct"].iloc[-1]
                     >= 0 else Direction.SHORT)
        return self.confidence_for(dataframe, direction)
