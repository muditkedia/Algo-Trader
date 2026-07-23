"""STRAT-03 - 5-minute Opening Drive Momentum."""

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
    add_opening_market_context, local_dates, shared_5m_features,
)


@dataclass(frozen=True)
class OpeningDriveParams:
    drive_bar_index: int = 1
    min_body_range_ratio: float = 0.70
    max_counter_wick_ratio: float = 0.15
    min_rvol_drive: float = 2.50
    rvol_sessions: int = 10
    atr_period: int = 14
    min_bar_range_atr: float = 0.75
    max_bar_range_atr: float = 2.50
    risk_per_trade_pct: float = 0.01
    max_capital_per_trade: float = 0.20
    stop_buffer_atr: float = 0.10
    tp1_r: float = 1.50
    tp1_fraction: float = 0.50
    chandelier_atr: float = 2.00
    slippage_collar_pct: float = 0.001
    no_progress_bars: int = 5
    no_progress_r: float = 0.50

    @classmethod
    def from_dict(cls, data) -> "OpeningDriveParams":
        return from_dict(cls, data)


class OpeningDriveMomentum(StrategyProfile):

    meta = StrategyMeta(
        name="opening_drive_5m", version="2.0.0", spec_id="STRAT-03",
        exclusive_group="opening_breakout",
        active_conflict_group="opening_or_retest",
        session_block_group="opening_drive",
        direction=Direction.BOTH, holding_scope=HoldingScope.INTRADAY,
        timeframe="5m", min_bars=1501,
        required_columns=(
            "close", "vwap", "atr", "rvol", "body_range_ratio",
            "lower_wick_ratio", "upper_wick_ratio", "drive_stop_long",
            "drive_stop_short", "bar_close_minute", "adt20", "prior_close",
            "nifty_open", "nifty_close"),
        supported_regimes=("opening_impulse",),
        hypothesis=("A high-RVOL opening candle with a dominant body and "
                    "negligible counter-wick reveals institutional order-flow "
                    "imbalance that persists immediately after uncrossing."),
        expected_behaviour=("A single long or short signal at 09:20 IST by "
                            "default, optionally 09:25 by configuration."),
        known_failure_modes=(
            "opening auction whipsaw after an apparently clean drive",
            "exhaustion bars wider than 2.5 ATR",
            "index divergence from the stock opening impulse",
        ), enabled=True)

    execution = ExecutionSpec(
        entry="limit_collar", slippage_collar_pct=0.001,
        stop_kind="column", stop_long_col="drive_stop_long",
        stop_short_col="drive_stop_short", hard_stop_pct=None,
        target_kind="r", target_r=1.5, partial_fraction=0.5,
        trail="chandelier", trail_atr_mult=2.0, trail_after_partial=True,
        invalidation_long_col="invalidate_long",
        invalidation_short_col="invalidate_short",
        no_progress_bars=5, no_progress_r=0.5,
        risk_per_trade_pct=0.01, max_capital_per_trade=0.20,
        intraday=True, allow_overnight=False, max_hold_bars=None)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or OpeningDriveParams())

    @property
    def context_symbols(self) -> tuple:
        return ("NIFTY50",)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = shared_5m_features(dataframe, p.atr_period, p.rvol_sessions)
        candle_range = (df["high"] - df["low"]).replace(0.0, np.nan)
        df["body_range_ratio"] = (df["close"] - df["open"]).abs() / candle_range
        df["lower_wick_ratio"] = (
            np.minimum(df["open"], df["close"]) - df["low"]) / candle_range
        df["upper_wick_ratio"] = (
            df["high"] - np.maximum(df["open"], df["close"])) / candle_range
        df["range_atr"] = candle_range / df["atr"].replace(0.0, np.nan)
        df["drive_stop_long"] = df["low"] - p.stop_buffer_atr * df["atr"]
        df["drive_stop_short"] = df["high"] + p.stop_buffer_atr * df["atr"]
        df["invalidate_long"] = df["close"] < df["vwap"]
        df["invalidate_short"] = df["close"] > df["vwap"]
        return df

    def prepare_context(self, frames: dict, context: dict) -> dict:
        return add_opening_market_context(frames, context)

    def _conditions(self, df: pd.DataFrame, direction: Direction) -> pd.Series:
        p = self.settings
        is_long = direction == Direction.LONG
        body = (df["close"] > df["open"] if is_long
                else df["close"] < df["open"])
        wick = df["lower_wick_ratio"] if is_long else df["upper_wick_ratio"]
        vwap = df["close"] > df["vwap"] if is_long else df["close"] < df["vwap"]
        nifty = (df["nifty_close"] > df["nifty_open"] if is_long
                 else df["nifty_close"] < df["nifty_open"])
        gap = df["open"] - df["prior_close"]
        gap_aligned = gap > 0 if is_long else gap < 0
        close_minute = 9 * 60 + 15 + p.drive_bar_index * 5
        return (body
                & (df["body_range_ratio"] >= p.min_body_range_ratio)
                & (wick <= p.max_counter_wick_ratio)
                & (df["rvol"] >= p.min_rvol_drive)
                & vwap
                & (df["range_atr"] >= p.min_bar_range_atr)
                & (df["range_atr"] <= p.max_bar_range_atr)
                & (df["bar_close_minute"] == close_minute)
                & (df["adt20"] >= 500_000_000)
                & nifty & gap_aligned).fillna(False)

    def entry_signals(self, dataframe: pd.DataFrame) -> dict:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            blank = self.no_signal(dataframe)
            return {Direction.LONG: blank, Direction.SHORT: blank.copy()}
        return {Direction.LONG: self._conditions(dataframe, Direction.LONG),
                Direction.SHORT: self._conditions(dataframe, Direction.SHORT)}

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        signals = self.entry_signals(dataframe)
        return signals[Direction.LONG] | signals[Direction.SHORT]

    def entry_trigger(self, dataframe: pd.DataFrame, index: int,
                      direction: Direction) -> float:
        return float(dataframe["close"].iloc[index])

    def grade_multiplier(self, confidence: float) -> float:
        # STRAT-03 specifies fixed fractional sizing without A/B/C grades.
        return 1.0

    def signal_priority(self, dataframe: pd.DataFrame, index: int,
                        direction: Direction, confidence: float,
                        regime: float) -> float:
        row = dataframe.iloc[index]
        return 0.60 * float(row["rvol"]) + 0.40 * float(
            row["body_range_ratio"])

    def signal_diagnostics(self, dataframe: pd.DataFrame,
                           symbol: str) -> list:
        if dataframe.empty or self.missing_columns(dataframe):
            return []
        p, row = self.settings, dataframe.iloc[-1]
        close_minute = 9 * 60 + 15 + p.drive_bar_index * 5
        if float(row["bar_close_minute"]) != close_minute:
            return []
        out = []
        for direction in (Direction.LONG, Direction.SHORT):
            is_long = direction == Direction.LONG
            directional = row["close"] > row["open"] if is_long \
                else row["close"] < row["open"]
            if not directional or self._conditions(dataframe, direction).iloc[-1]:
                continue
            wick = row["lower_wick_ratio"] if is_long \
                else row["upper_wick_ratio"]
            checks = [
                (row["body_range_ratio"] >= p.min_body_range_ratio, "body"),
                (wick <= p.max_counter_wick_ratio, "counter_wick"),
                (row["rvol"] >= p.min_rvol_drive, "rvol"),
                ((row["close"] > row["vwap"]) if is_long
                 else (row["close"] < row["vwap"]), "vwap"),
                (p.min_bar_range_atr <= row["range_atr"]
                 <= p.max_bar_range_atr, "range_atr"),
                ((row["nifty_close"] > row["nifty_open"]) if is_long
                 else (row["nifty_close"] < row["nifty_open"]), "nifty"),
            ]
            out.append({
                "symbol": symbol, "strategy": self.name,
                "direction": direction.value, "accepted": False,
                "reasons": [name for passed, name in checks if not passed],
                "rvol": float(row["rvol"]),
                "body_range_ratio": float(row["body_range_ratio"]),
                "bar_time": str(pd.Timestamp(row["date"])),
            })
        return out

    def confidence_for(self, dataframe: pd.DataFrame,
                       direction: Direction) -> ConfidenceScore:
        required = {"body_range_ratio", "rvol"}
        if dataframe.empty or not required.issubset(dataframe.columns):
            return ConfidenceScore.zero("unprepared frame")
        row = dataframe.iloc[-1]
        body = clip01((float(row["body_range_ratio"]) - 0.60) / 0.40)
        volume = clip01(float(row["rvol"]) / 4.0)
        score = 0.40 * body + 0.60 * volume
        return ConfidenceScore(
            score=score,
            components={
                "body_geometry": {"score": body, "weight": 0.40,
                                  "detail": f"BRR {row['body_range_ratio']:.2f}"},
                "opening_rvol": {"score": volume, "weight": 0.60,
                                 "detail": f"RVOL {row['rvol']:.2f}"},
            }, reason=f"STRAT-03 {direction.value} opening drive quality")

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if dataframe.empty:
            return ConfidenceScore.zero("empty frame")
        direction = (Direction.LONG if dataframe["close"].iloc[-1]
                     >= dataframe["open"].iloc[-1] else Direction.SHORT)
        return self.confidence_for(dataframe, direction)
