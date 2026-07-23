"""STRAT-04 - canonical 5-minute Gap & Go Acceleration strategy."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import (
    atr, ema, opening_range, session_vwap, slot_relative_volume,
)
from algo.execution import ExecutionSpec
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import ConfidenceScore, clip01
from algo.strategies.opening_context import (
    add_opening_market_context, local_dates, shared_5m_features,
)


@dataclass(frozen=True)
class GapGoParams:
    min_gap_pct: float = 0.010
    max_gap_pct: float = 0.035
    min_rvol_gap: float = 2.50
    short_rvol_add: float = 0.50
    max_gap_fill_pct: float = 0.20
    max_entry_window_mins: int = 75
    rvol_sessions: int = 10
    atr_period: int = 14
    risk_per_trade_pct: float = 0.01
    max_capital_per_trade: float = 0.20
    tp1_r: float = 1.50
    tp1_fraction: float = 0.50
    chandelier_atr: float = 2.00
    max_stop_atr: float = 1.25
    slippage_collar_pct: float = 0.001
    no_progress_bars: int = 6
    no_progress_r: float = 0.50
    nifty_gap_min_pct: float = 0.003

    @classmethod
    def from_dict(cls, data) -> "GapGoParams":
        return from_dict(cls, data)


class GapAndGo(StrategyProfile):

    meta = StrategyMeta(
        name="gapgo_5m", version="2.0.0", spec_id="STRAT-04",
        session_block_group="gap_go", direction=Direction.BOTH,
        holding_scope=HoldingScope.INTRADAY, timeframe="5m", min_bars=1502,
        required_columns=(
            "close", "vwap", "atr", "gap_pct", "opening_high",
            "opening_low", "opening_open", "opening_close", "opening_rvol",
            "retained_long", "retained_short", "bar_close_minute", "adt20",
            "ema9", "ema20", "nifty_gap_pct", "stop_long", "stop_short"),
        supported_regimes=("opening_gap_momentum",),
        hypothesis=("A liquid 1.0%-3.5% overnight gap that retains at least "
                    "80% of its distance on exceptional opening volume can "
                    "accelerate when price breaks the opening candle."),
        expected_behaviour=("At most one bidirectional gap continuation "
                            "entry per symbol/session by 10:30 IST."),
        known_failure_modes=(
            "exhaustion gap reversals after a marginal opening break",
            "index gap divergence and broad-market liquidation",
            "news gaps whose apparent candle liquidity is not executable",
        ), enabled=True)

    execution = ExecutionSpec(
        entry="limit_collar", slippage_collar_pct=0.001,
        stop_kind="column", stop_long_col="stop_long",
        stop_short_col="stop_short", hard_stop_pct=None,
        target_kind="r", target_r=1.5, partial_fraction=0.5,
        trail="chandelier", trail_atr_mult=2.0, trail_after_partial=True,
        invalidation_long_col="invalidate_long",
        invalidation_short_col="invalidate_short",
        no_progress_bars=6, no_progress_r=0.5,
        risk_per_trade_pct=0.01, max_capital_per_trade=0.20,
        intraday=True, allow_overnight=False, max_hold_bars=None)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or GapGoParams())

    @property
    def context_symbols(self) -> tuple:
        return ("NIFTY50",)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = shared_5m_features(dataframe, p.atr_period, p.rvol_sessions)
        local = local_dates(df)
        day = local.dt.normalize()
        opening_high, opening_low, after = opening_range(df, 5)
        df["opening_high"], df["opening_low"] = opening_high, opening_low
        df["after_opening"] = after
        df["opening_open"] = df["open"].groupby(day).transform("first")
        df["opening_close"] = df["close"].groupby(day).transform("first")
        df["opening_rvol"] = df["rvol"].where(~after).groupby(day).transform(
            "first")
        df["gap_pct"] = df["opening_open"] / df["prior_close"] - 1.0
        gap_distance = (df["opening_open"] - df["prior_close"]).abs()
        df["retained_long"] = (
            df["opening_low"] > df["prior_close"]
            + (1.0 - p.max_gap_fill_pct) * gap_distance)
        df["retained_short"] = (
            df["opening_high"] < df["prior_close"]
            - (1.0 - p.max_gap_fill_pct) * gap_distance)
        df["stop_long"] = np.minimum(
            df["opening_low"], df["close"] - p.max_stop_atr * df["atr"])
        df["stop_short"] = np.maximum(
            df["opening_high"], df["close"] + p.max_stop_atr * df["atr"])
        df["invalidate_long"] = df["close"] < df["vwap"]
        df["invalidate_short"] = df["close"] > df["vwap"]
        return df

    def prepare_context(self, frames: dict, context: dict) -> dict:
        return add_opening_market_context(frames, context)

    def _conditions(self, df: pd.DataFrame,
                    direction: Direction) -> pd.Series:
        p = self.settings
        is_long = direction == Direction.LONG
        gap = df["gap_pct"] if is_long else -df["gap_pct"]
        retained = df["retained_long"] if is_long else df["retained_short"]
        opening_body = (df["opening_close"] > df["opening_open"] if is_long
                        else df["opening_close"] < df["opening_open"])
        current_body = (df["close"] > df["open"] if is_long
                        else df["close"] < df["open"])
        breakout = (df["close"] > df["opening_high"] if is_long
                    else df["close"] < df["opening_low"])
        vwap = df["close"] > df["vwap"] if is_long else df["close"] < df["vwap"]
        ema_aligned = df["ema9"] > df["ema20"] if is_long else df["ema9"] < df["ema20"]
        nifty_gap = (df["nifty_gap_pct"] >= p.nifty_gap_min_pct if is_long
                     else df["nifty_gap_pct"] <= -p.nifty_gap_min_pct)
        required_rvol = p.min_rvol_gap + (0.0 if is_long else p.short_rvol_add)
        in_window = ((df["bar_close_minute"] >= 9 * 60 + 20)
                     & (df["bar_close_minute"] <= 9 * 60 + 15
                        + p.max_entry_window_mins))
        raw = ((gap >= p.min_gap_pct) & (gap <= p.max_gap_pct)
               & retained & opening_body & current_body & breakout
               & (df["opening_rvol"] >= required_rvol) & vwap & in_window
               & (df["adt20"] >= 500_000_000) & ema_aligned & nifty_gap)
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
        return 0.50 * abs(float(row["gap_pct"])) + 0.50 * float(
            row["opening_rvol"])

    def confidence_for(self, dataframe: pd.DataFrame,
                       direction: Direction) -> ConfidenceScore:
        required = {"gap_pct", "opening_rvol"}
        if dataframe.empty or not required.issubset(dataframe.columns):
            return ConfidenceScore.zero("unprepared frame")
        row = dataframe.iloc[-1]
        gap = clip01((abs(float(row["gap_pct"])) - self.settings.min_gap_pct)
                     / (self.settings.max_gap_pct - self.settings.min_gap_pct))
        volume = clip01(float(row["opening_rvol"]) / 4.0)
        score = 0.50 * gap + 0.50 * volume
        return ConfidenceScore(
            score=score,
            components={
                "gap_size": {"score": gap, "weight": 0.50,
                             "detail": f"gap {row['gap_pct']:+.2%}"},
                "opening_rvol": {"score": volume, "weight": 0.50,
                                 "detail": f"RVOL {row['opening_rvol']:.2f}"},
            }, reason=f"STRAT-04 {direction.value} gap acceleration")

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if dataframe.empty or "gap_pct" not in dataframe.columns:
            return ConfidenceScore.zero("unprepared frame")
        direction = (Direction.LONG if dataframe["gap_pct"].iloc[-1] >= 0
                     else Direction.SHORT)
        return self.confidence_for(dataframe, direction)
