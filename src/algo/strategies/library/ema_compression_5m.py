"""STRAT-09 - canonical 5-minute EMA Compression Breakout."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import (
    atr, bollinger_bandwidth, directional_movement, ema, session_vwap,
    slot_relative_volume,
)
from algo.execution import ExecutionSpec
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import ConfidenceScore, clip01
from algo.strategies.opening_context import (
    add_opening_market_context, local_dates, prior_session_metrics,
)


@dataclass(frozen=True)
class EmaCompressionParams:
    ema_fast: int = 8
    ema_mid: int = 20
    ema_slow: int = 50
    ema_macro: int = 200
    max_ema_spread_pct: float = 0.0035
    min_compression_bars: int = 4
    breakout_buffer_pct: float = 0.0010
    min_rvol_breakout: float = 2.00
    short_rvol_add: float = 0.50
    rvol_sessions: int = 10
    atr_period: int = 14
    stop_buffer_atr: float = 0.10
    stop_cap_atr: float = 1.25
    risk_per_trade_pct: float = 0.01
    max_capital_per_trade: float = 0.20
    tp1_r: float = 1.50
    tp1_fraction: float = 0.50
    chandelier_atr: float = 2.00
    slippage_collar_pct: float = 0.001
    no_progress_bars: int = 6
    no_progress_r: float = 0.40
    bb_period: int = 20
    bb_std: float = 2.0
    bb_low_lookback: int = 10
    use_bb_contraction: bool = True
    use_nifty_alignment: bool = True
    use_adx_expansion: bool = True

    @classmethod
    def from_dict(cls, data) -> "EmaCompressionParams":
        return from_dict(cls, data)


class EMACompressionBreakout(StrategyProfile):

    meta = StrategyMeta(
        name="ema_compression_5m", version="2.0.0", spec_id="STRAT-09",
        pre_partial_block_group="ema_compression",
        active_block_group="primary_trend",
        direction=Direction.BOTH, holding_scope=HoldingScope.INTRADAY,
        timeframe="5m", min_bars=1501,
        required_columns=(
            "close", "ema8", "ema20", "ema50", "ema200", "ema_spread",
            "compression_ready", "compression_low", "compression_high",
            "vwap", "rvol", "atr", "body_range_ratio", "bbw_at_low",
            "adx14", "plus_di", "minus_di", "nifty_close", "nifty_ema20",
            "adt20", "bar_close_minute", "stop_long", "stop_short"),
        supported_regimes=("volatility_expansion", "intraday_trend"),
        hypothesis=("Four completed bars of EMA8/20/50 convergence create a "
                    "coiled equilibrium whose buffered, high-RVOL release in "
                    "the EMA200/VWAP direction can expand rapidly."),
        expected_behaviour=("Bidirectional 5-minute breakout after a causal "
                            "four-bar compressed ribbon between 09:30 and "
                            "14:45."),
        known_failure_modes=(
            "midday low-volume false breakouts",
            "wide or already-expanded moving-average ribbons",
            "breaks directly into nearby support or resistance",
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
        super().__init__(settings or EmaCompressionParams())

    @property
    def context_symbols(self) -> tuple:
        return ("NIFTY50",)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy().reset_index(drop=True)
        local = local_dates(df)
        day = local.dt.normalize()
        df["ema8"] = ema(df["close"], p.ema_fast)
        df["ema20"] = ema(df["close"], p.ema_mid)
        df["ema50"] = ema(df["close"], p.ema_slow)
        df["ema200"] = ema(df["close"], p.ema_macro)
        ribbon_max = df[["ema8", "ema20", "ema50"]].max(axis=1)
        ribbon_min = df[["ema8", "ema20", "ema50"]].min(axis=1)
        df["ema_spread"] = ((ribbon_max - ribbon_min)
                            / df["ema50"].replace(0.0, np.nan))
        df["compression_ready"] = df["ema_spread"].groupby(day).transform(
            lambda values: values.shift(1).rolling(
                p.min_compression_bars,
                min_periods=p.min_compression_bars).max()
        ) <= p.max_ema_spread_pct
        df["compression_low"] = df["low"].groupby(day).transform(
            lambda values: values.shift(1).rolling(
                p.min_compression_bars,
                min_periods=p.min_compression_bars).min())
        df["compression_high"] = df["high"].groupby(day).transform(
            lambda values: values.shift(1).rolling(
                p.min_compression_bars,
                min_periods=p.min_compression_bars).max())
        df["vwap"] = session_vwap(df)
        df["rvol"] = slot_relative_volume(df, p.rvol_sessions)
        df["atr"] = atr(df, p.atr_period)
        candle_range = (df["high"] - df["low"]).replace(0.0, np.nan)
        df["body_range_ratio"] = (
            (df["close"] - df["open"]).abs() / candle_range)
        df["bbw"] = bollinger_bandwidth(
            df["close"], p.bb_period, p.bb_std)
        prior_bbw = df["bbw"].groupby(day).transform(lambda values: values.shift(1))
        prior_low = df["bbw"].groupby(day).transform(
            lambda values: values.shift(1).rolling(
                p.bb_low_lookback,
                min_periods=p.bb_low_lookback).min())
        df["bbw_at_low"] = prior_bbw <= prior_low
        df["plus_di"], df["minus_di"], df["adx14"] = directional_movement(
            df, p.atr_period)
        prior_close, adt20, _, _ = prior_session_metrics(df)
        df["prior_close"], df["adt20"] = prior_close, adt20
        df["bar_close_minute"] = local.dt.hour * 60 + local.dt.minute + 5
        structural_long = (df["compression_low"]
                           - p.stop_buffer_atr * df["atr"])
        structural_short = (df["compression_high"]
                            + p.stop_buffer_atr * df["atr"])
        df["stop_long"] = np.maximum(
            structural_long, df["close"] - p.stop_cap_atr * df["atr"])
        df["stop_short"] = np.minimum(
            structural_short, df["close"] + p.stop_cap_atr * df["atr"])
        df["invalidate_long"] = df["close"] < df["ema20"]
        df["invalidate_short"] = df["close"] > df["ema20"]
        return df

    def prepare_context(self, frames: dict, context: dict) -> dict:
        return add_opening_market_context(frames, context)

    def _conditions(self, df: pd.DataFrame,
                    direction: Direction) -> pd.Series:
        p = self.settings
        is_long = direction == Direction.LONG
        ribbon_edge = (df[["ema8", "ema20", "ema50"]].max(axis=1)
                       if is_long else
                       df[["ema8", "ema20", "ema50"]].min(axis=1))
        macro = df["close"] > df["ema200"] if is_long \
            else df["close"] < df["ema200"]
        vwap = df["close"] > df["vwap"] if is_long \
            else df["close"] < df["vwap"]
        breakout = (df["close"] > ribbon_edge * (1 + p.breakout_buffer_pct)
                    if is_long else
                    df["close"] < ribbon_edge * (1 - p.breakout_buffer_pct))
        required_rvol = p.min_rvol_breakout + (0.0 if is_long
                                                else p.short_rvol_add)
        body = ((df["close"] > df["open"]) if is_long
                else (df["close"] < df["open"]))
        nifty = ((df["nifty_close"] > df["nifty_ema20"]) if is_long
                 else (df["nifty_close"] < df["nifty_ema20"]))
        adx_expansion = ((df["adx14"] > df["adx14"].shift(1))
                         & ((df["plus_di"] > df["minus_di"]) if is_long
                            else (df["minus_di"] > df["plus_di"])))
        in_window = ((df["bar_close_minute"] >= 9 * 60 + 30)
                     & (df["bar_close_minute"] <= 14 * 60 + 45))
        conditions = (df["compression_ready"] & macro & vwap & breakout
                      & (df["rvol"] >= required_rvol) & body
                      & (df["body_range_ratio"] >= 0.50) & in_window
                      & (df["adt20"] >= 500_000_000))
        if p.use_bb_contraction:
            conditions &= df["bbw_at_low"]
        if p.use_nifty_alignment:
            conditions &= nifty
        if p.use_adx_expansion:
            conditions &= adx_expansion
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
        spread = max(abs(float(row["ema_spread"])), 1e-12)
        return 0.50 * float(row["rvol"]) + 0.50 / spread

    def confidence_for(self, dataframe: pd.DataFrame,
                       direction: Direction) -> ConfidenceScore:
        required = {"ema_spread", "rvol"}
        if dataframe.empty or not required.issubset(dataframe.columns):
            return ConfidenceScore.zero("unprepared frame")
        row = dataframe.iloc[-1]
        spread_value = max(abs(float(row["ema_spread"])), 1e-12)
        tightness = clip01(1.0 - spread_value / self.settings.max_ema_spread_pct)
        volume = clip01(float(row["rvol"]) / 3.5)
        return ConfidenceScore(
            score=0.50 * tightness + 0.50 * volume,
            components={
                "ribbon_tightness": {
                    "score": tightness, "weight": 0.50,
                    "detail": f"spread {spread_value:.4%}"},
                "breakout_rvol": {
                    "score": volume, "weight": 0.50,
                    "detail": f"RVOL {row['rvol']:.2f}"},
            }, reason=f"STRAT-09 {direction.value} compression breakout")

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if dataframe.empty:
            return ConfidenceScore.zero("empty frame")
        direction = (Direction.LONG if dataframe["close"].iloc[-1]
                     >= dataframe["open"].iloc[-1] else Direction.SHORT)
        return self.confidence_for(dataframe, direction)
