"""STRAT-06 - 5-minute Initial Balance Breakout."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import (
    atr, central_pivot_range, ema, opening_range, session_vwap,
    slot_relative_volume,
)
from algo.execution import ExecutionSpec
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import ConfidenceScore, clip01
from algo.strategies.opening_context import (
    add_opening_market_context, local_dates, prior_session_metrics,
)


@dataclass(frozen=True)
class InitialBalanceParams:
    ib_duration_mins: int = 30
    breakout_buffer_pct: float = 0.001
    max_ib_width_atr_mult: float = 2.20
    min_ib_width_atr_mult: float = 0.60
    min_rvol_ibb: float = 2.00
    short_rvol_add: float = 0.50
    rvol_sessions: int = 10
    atr_period: int = 14
    max_stop_atr: float = 1.50
    risk_per_trade_pct: float = 0.01
    max_capital_per_trade: float = 0.20
    tp1_r: float = 1.50
    tp1_fraction: float = 0.50
    chandelier_atr: float = 2.00
    slippage_collar_pct: float = 0.001
    no_progress_bars: int = 6
    no_progress_r: float = 0.40

    @classmethod
    def from_dict(cls, data) -> "InitialBalanceParams":
        return from_dict(cls, data)


class InitialBalanceBreakout(StrategyProfile):

    meta = StrategyMeta(
        name="initial_balance_5m", version="2.0.0", spec_id="STRAT-06",
        direction=Direction.BOTH, holding_scope=HoldingScope.INTRADAY,
        timeframe="5m", min_bars=1507,
        required_columns=(
            "close", "ib_high", "ib_low", "ib_mid", "ib_width", "after_ib",
            "atr", "vwap", "rvol", "ema9", "ema20", "adt20",
            "prior_close",
            "bar_close_minute", "nifty_close", "nifty_ib_high",
            "nifty_ib_low", "cpr_top", "cpr_bottom"),
        supported_regimes=("initial_balance_expansion",),
        hypothesis=("A high-volume break from the first 30-minute auction "
                    "range reveals other-timeframe acceptance and directional "
                    "value expansion after opening noise stabilizes."),
        expected_behaviour=("At most one buffered 30-minute IB break per "
                            "symbol/session between 09:45 and 14:45 IST."),
        known_failure_modes=(
            "wide initial balances with exhausted reward-to-risk",
            "low-volume false acceptance followed by bracket rotation",
            "breakouts directly into daily CPR congestion",
        ), enabled=True)

    execution = ExecutionSpec(
        entry="limit_collar", slippage_collar_pct=0.001,
        stop_kind="column_atr_cap", stop_col="ib_mid", stop_atr_mult=1.5,
        hard_stop_pct=None, target_kind="r", target_r=1.5,
        partial_fraction=0.5, trail="chandelier", trail_atr_mult=2.0,
        trail_after_partial=True,
        invalidation_long_col="invalidate_long",
        invalidation_short_col="invalidate_short",
        no_progress_bars=6, no_progress_r=0.4,
        risk_per_trade_pct=0.01, max_capital_per_trade=0.20,
        intraday=True, allow_overnight=False, max_hold_bars=None)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or InitialBalanceParams())

    @property
    def context_symbols(self) -> tuple:
        return ("NIFTY50",)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy().reset_index(drop=True)
        df["ib_high"], df["ib_low"], df["after_ib"] = opening_range(
            df, p.ib_duration_mins)
        df["ib_mid"] = (df["ib_high"] + df["ib_low"]) / 2.0
        df["ib_width"] = df["ib_high"] - df["ib_low"]
        df["atr"] = atr(df, p.atr_period)
        df["vwap"] = session_vwap(df)
        df["rvol"] = slot_relative_volume(df, p.rvol_sessions)
        df["ema9"], df["ema20"] = ema(df["close"], 9), ema(df["close"], 20)
        _, cpr_top, cpr_bottom = central_pivot_range(df)
        df["cpr_top"], df["cpr_bottom"] = cpr_top, cpr_bottom
        prior_close, adt20, _, _ = prior_session_metrics(df)
        df["prior_close"], df["adt20"] = prior_close, adt20
        local = local_dates(df)
        df["bar_close_minute"] = local.dt.hour * 60 + local.dt.minute + 5
        df["invalidate_long"] = df["close"] < df["vwap"]
        df["invalidate_short"] = df["close"] > df["vwap"]
        return df

    def prepare_context(self, frames: dict, context: dict) -> dict:
        return add_opening_market_context(frames, context)

    def _conditions(self, df: pd.DataFrame,
                    direction: Direction) -> pd.Series:
        p = self.settings
        is_long = direction == Direction.LONG
        width_ratio = df["ib_width"] / df["atr"].where(df["atr"] != 0.0)
        threshold = (df["ib_high"] * (1.0 + p.breakout_buffer_pct)
                     if is_long else
                     df["ib_low"] * (1.0 - p.breakout_buffer_pct))
        breakout = df["close"] > threshold if is_long else df["close"] < threshold
        required_rvol = p.min_rvol_ibb + (0.0 if is_long else p.short_rvol_add)
        vwap = df["close"] > df["vwap"] if is_long else df["close"] < df["vwap"]
        ema_aligned = df["ema9"] > df["ema20"] if is_long else df["ema9"] < df["ema20"]
        nifty_ib = (df["nifty_close"] > df["nifty_ib_high"] if is_long
                    else df["nifty_close"] < df["nifty_ib_low"])
        cpr_clear = df["close"] > df["cpr_top"] if is_long else df["close"] < df["cpr_bottom"]
        in_window = ((df["bar_close_minute"] >= 9 * 60 + 45)
                     & (df["bar_close_minute"] <= 14 * 60 + 45))
        raw = (df["after_ib"]
               & (width_ratio >= p.min_ib_width_atr_mult)
               & (width_ratio <= p.max_ib_width_atr_mult)
               & breakout & (df["rvol"] >= required_rvol) & vwap & ema_aligned
               & in_window & (df["adt20"] >= 500_000_000)
               & nifty_ib & cpr_clear)
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
        return 0.50 * float(row["rvol"]) + 0.50 * (
            float(row["atr"]) / float(row["ib_width"]))

    def confidence_for(self, dataframe: pd.DataFrame,
                       direction: Direction) -> ConfidenceScore:
        required = {"rvol", "atr", "ib_width"}
        if dataframe.empty or not required.issubset(dataframe.columns):
            return ConfidenceScore.zero("unprepared frame")
        row = dataframe.iloc[-1]
        volume = clip01(float(row["rvol"]) / 3.0)
        compression = clip01(float(row["atr"]) / float(row["ib_width"]))
        return ConfidenceScore(
            score=0.50 * volume + 0.50 * compression,
            components={
                "breakout_rvol": {"score": volume, "weight": 0.50,
                                  "detail": f"RVOL {row['rvol']:.2f}"},
                "ib_compression": {"score": compression, "weight": 0.50,
                                   "detail": f"IB {row['ib_width']:.2f}"},
            }, reason=f"STRAT-06 {direction.value} initial-balance break")

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if dataframe.empty or not {"ib_high", "ib_low"}.issubset(dataframe.columns):
            return ConfidenceScore.zero("unprepared frame")
        midpoint = (dataframe["ib_high"].iloc[-1]
                    + dataframe["ib_low"].iloc[-1]) / 2.0
        direction = (Direction.LONG if dataframe["close"].iloc[-1] >= midpoint
                     else Direction.SHORT)
        return self.confidence_for(dataframe, direction)
