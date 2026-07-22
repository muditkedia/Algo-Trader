"""STRAT-07 - 5-minute Opening Liquidity Sweep & Reversal."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import (
    atr, central_pivot_range, opening_range, prior_session_ohlc, rsi,
    session_vwap, slot_relative_volume,
)
from algo.execution import ExecutionSpec
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import ConfidenceScore, clip01
from algo.strategies.opening_context import (
    add_opening_market_context, local_dates, prior_session_metrics,
)


@dataclass(frozen=True)
class LiquiditySweepParams:
    min_wick_ratio: float = 0.40
    max_sweep_atr_mult: float = 0.50
    min_rvol_sweep: float = 2.00
    rvol_sessions: int = 10
    atr_period: int = 14
    stop_buffer_atr: float = 0.10
    risk_per_trade_pct: float = 0.01
    max_capital_per_trade: float = 0.20
    tp1_r: float = 1.50
    tp1_fraction: float = 0.50
    chandelier_atr: float = 1.75
    slippage_collar_pct: float = 0.001
    timeout_bars: int = 6
    level_tolerance_pct: float = 0.0015

    @classmethod
    def from_dict(cls, data) -> "LiquiditySweepParams":
        return from_dict(cls, data)


class OpeningLiquiditySweep(StrategyProfile):

    meta = StrategyMeta(
        name="liquidity_sweep_5m", version="2.0.0", spec_id="STRAT-07",
        timed_block_group="liquidity_sweep", timed_block_minutes=60,
        direction=Direction.BOTH, holding_scope=HoldingScope.INTRADAY,
        timeframe="5m", min_bars=1502,
        required_columns=(
            "close", "or_high", "or_low", "boundary_long", "boundary_short",
            "after_or", "atr", "vwap", "rvol", "rsi", "lower_wick_ratio",
            "upper_wick_ratio", "sweep_depth_long", "sweep_depth_short",
            "adt20", "bar_close_minute", "nifty_high", "nifty_low",
            "nifty_or_high", "nifty_or_low", "stop_long", "stop_short",
            "tp1_r_long", "tp1_r_short", "target2_long", "target2_short"),
        supported_regimes=("opening_rotation", "mean_reversion"),
        hypothesis=("A shallow high-volume stop run through the nearest "
                    "opening/prior-day boundary that closes back inside with "
                    "a dominant rejection wick traps breakout participants."),
        expected_behaviour=("At most one counter-sweep entry per symbol and "
                            "session between 09:20 and 10:30 IST."),
        known_failure_modes=(
            "fundamental trend days where the sweep becomes continuation",
            "deep structural pierces beyond 0.5 ATR",
            "low-volume wicks without institutional absorption",
        ), enabled=True)

    execution = ExecutionSpec(
        entry="limit_collar", slippage_collar_pct=0.001,
        stop_kind="column", stop_long_col="stop_long",
        stop_short_col="stop_short", hard_stop_pct=None,
        target_kind="r", target_r=1.5,
        target_r_long_col="tp1_r_long", target_r_short_col="tp1_r_short",
        partial_fraction=0.5, target2_long_col="target2_long",
        target2_short_col="target2_short",
        trail="chandelier", trail_atr_mult=1.75, trail_after_partial=True,
        timeout_bars=6, timeout_target_long_col="vwap",
        timeout_target_short_col="vwap",
        risk_per_trade_pct=0.01, max_capital_per_trade=0.20,
        intraday=True, allow_overnight=False, max_hold_bars=None)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or LiquiditySweepParams())

    @property
    def context_symbols(self) -> tuple:
        return ("NIFTY50",)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy().reset_index(drop=True)
        df["or_high"], df["or_low"], df["after_or"] = opening_range(df, 5)
        _, prior_high, prior_low, prior_close = prior_session_ohlc(df)
        df["prior_high"], df["prior_low"] = prior_high, prior_low
        df["prior_close"] = prior_close
        df["boundary_long"] = pd.concat(
            [df["or_low"], prior_low], axis=1).max(axis=1)
        df["boundary_short"] = pd.concat(
            [df["or_high"], prior_high], axis=1).min(axis=1)
        df["atr"] = atr(df, p.atr_period)
        df["vwap"] = session_vwap(df)
        df["rvol"] = slot_relative_volume(df, p.rvol_sessions)
        df["rsi"] = rsi(df["close"], 14)
        candle_range = (df["high"] - df["low"]).where(
            df["high"] != df["low"])
        df["lower_wick_ratio"] = (
            np.minimum(df["open"], df["close"]) - df["low"]) / candle_range
        df["upper_wick_ratio"] = (
            df["high"] - np.maximum(df["open"], df["close"])) / candle_range
        df["sweep_depth_long"] = df["boundary_long"] - df["low"]
        df["sweep_depth_short"] = df["high"] - df["boundary_short"]
        _, cpr_top, cpr_bottom = central_pivot_range(df)
        df["cpr_top"], df["cpr_bottom"] = cpr_top, cpr_bottom
        _, adt20, _, _ = prior_session_metrics(df)
        df["adt20"] = adt20
        local = local_dates(df)
        df["bar_close_minute"] = local.dt.hour * 60 + local.dt.minute + 5
        df["stop_long"] = df["low"] - p.stop_buffer_atr * df["atr"]
        df["stop_short"] = df["high"] + p.stop_buffer_atr * df["atr"]
        risk_long = df["close"] - df["stop_long"]
        risk_short = df["stop_short"] - df["close"]
        vwap_r_long = (df["vwap"] - df["close"]) / risk_long
        vwap_r_short = (df["close"] - df["vwap"]) / risk_short
        df["tp1_r_long"] = vwap_r_long.where(vwap_r_long > 0.0, p.tp1_r).clip(
            upper=p.tp1_r)
        df["tp1_r_short"] = vwap_r_short.where(
            vwap_r_short > 0.0, p.tp1_r).clip(upper=p.tp1_r)
        df["target2_long"], df["target2_short"] = df["or_high"], df["or_low"]
        return df

    def prepare_context(self, frames: dict, context: dict) -> dict:
        return add_opening_market_context(frames, context)

    def _level_confluence(self, df: pd.DataFrame,
                          direction: Direction) -> pd.Series:
        boundary = (df["boundary_long"] if direction == Direction.LONG
                    else df["boundary_short"])
        prior = df["prior_low"] if direction == Direction.LONG else df["prior_high"]
        distances = pd.concat([
            (boundary - df["cpr_top"]).abs(),
            (boundary - df["cpr_bottom"]).abs(),
            (boundary - prior).abs()], axis=1)
        return distances.min(axis=1) / boundary.where(boundary != 0.0) \
            <= self.settings.level_tolerance_pct

    def _conditions(self, df: pd.DataFrame,
                    direction: Direction) -> pd.Series:
        p = self.settings
        is_long = direction == Direction.LONG
        boundary = df["boundary_long"] if is_long else df["boundary_short"]
        swept = df["low"] < boundary if is_long else df["high"] > boundary
        depth = df["sweep_depth_long"] if is_long else df["sweep_depth_short"]
        wick = df["lower_wick_ratio"] if is_long else df["upper_wick_ratio"]
        body = df["close"] > df["open"] if is_long else df["close"] < df["open"]
        reclaim = df["close"] >= boundary if is_long else df["close"] <= boundary
        rsi_extreme = df["rsi"] < 30.0 if is_long else df["rsi"] > 70.0
        nifty_nonconfirm = (df["nifty_low"] >= df["nifty_or_low"] if is_long
                            else df["nifty_high"] <= df["nifty_or_high"])
        in_window = ((df["bar_close_minute"] >= 9 * 60 + 20)
                     & (df["bar_close_minute"] <= 10 * 60 + 30))
        raw = (df["after_or"] & swept & (depth <= p.max_sweep_atr_mult * df["atr"])
               & (wick >= p.min_wick_ratio) & body & reclaim
               & (df["rvol"] >= p.min_rvol_sweep) & in_window
               & (df["adt20"] >= 500_000_000) & rsi_extreme
               & nifty_nonconfirm & self._level_confluence(df, direction))
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
        wick = float(row["lower_wick_ratio" if direction == Direction.LONG
                         else "upper_wick_ratio"])
        return 0.50 * wick + 0.50 * float(row["rvol"])

    def confidence_for(self, dataframe: pd.DataFrame,
                       direction: Direction) -> ConfidenceScore:
        required = {"rvol", "lower_wick_ratio", "upper_wick_ratio"}
        if dataframe.empty or not required.issubset(dataframe.columns):
            return ConfidenceScore.zero("unprepared frame")
        row = dataframe.iloc[-1]
        wick_value = float(row["lower_wick_ratio" if direction == Direction.LONG
                               else "upper_wick_ratio"])
        wick = clip01(wick_value)
        volume = clip01(float(row["rvol"]) / 3.0)
        return ConfidenceScore(
            score=0.50 * wick + 0.50 * volume,
            components={
                "rejection_wick": {"score": wick, "weight": 0.50,
                                   "detail": f"wick {wick_value:.1%}"},
                "sweep_rvol": {"score": volume, "weight": 0.50,
                               "detail": f"RVOL {row['rvol']:.2f}"},
            }, reason=f"STRAT-07 {direction.value} liquidity sweep")

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if dataframe.empty or not {"lower_wick_ratio", "upper_wick_ratio"}.issubset(
                dataframe.columns):
            return ConfidenceScore.zero("unprepared frame")
        direction = (Direction.LONG
                     if dataframe["lower_wick_ratio"].iloc[-1]
                     >= dataframe["upper_wick_ratio"].iloc[-1]
                     else Direction.SHORT)
        return self.confidence_for(dataframe, direction)
