"""STRAT-11 - Donchian Volatility Expansion (DVE), 5-minute."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import donchian_channel
from algo.execution import ExecutionSpec
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import ConfidenceScore, clip01
from algo.strategies.opening_context import (
    add_opening_market_context, local_dates, shared_5m_features,
)


@dataclass(frozen=True)
class DonchianVolatilityExpansionParams:
    lookback_bars: int = 20
    breakout_buffer_pct: float = 0.0010
    min_ver: float = 1.15
    max_channel_width_atr: float = 2.50
    min_rvol: float = 1.80
    short_rvol_add: float = 0.40
    rvol_sessions: int = 10
    atr_period: int = 14
    atr_average_period: int = 20
    stop_cap_atr: float = 1.25
    tp1_r: float = 1.50
    tp1_fraction: float = 0.50
    chandelier_atr: float = 2.00
    risk_per_trade_pct: float = 0.01
    max_capital_per_trade: float = 0.20
    slippage_collar_pct: float = 0.001
    no_progress_bars: int = 6
    no_progress_r: float = 0.40
    entry_start_minute: int = 9 * 60 + 30
    entry_end_minute: int = 14 * 60 + 45
    use_nifty_alignment: bool = True
    use_close_strength: bool = True

    @classmethod
    def from_dict(cls, data) -> "DonchianVolatilityExpansionParams":
        return from_dict(cls, data)


class DonchianVolatilityExpansion(StrategyProfile):

    meta = StrategyMeta(
        name="donchian_volatility_expansion_5m", version="2.0.0",
        spec_id="STRAT-11", direction=Direction.BOTH,
        holding_scope=HoldingScope.INTRADAY, timeframe="5m", min_bars=1501,
        pre_partial_block_group="dve_breakout",
        simultaneous_priority_over=("orb_5m",),
        required_columns=(
            "close", "dc_upper", "dc_lower", "dc_mid", "dc_width", "atr",
            "ver", "ema9", "ema20", "vwap", "rvol", "adt20",
            "bar_close_minute", "nifty_close", "nifty_dc_upper20",
            "nifty_dc_lower20"),
        supported_regimes=("volatility_expansion", "intraday_trend"),
        hypothesis=("A prior-only 20-bar channel break accompanied by rising "
                    "ATR and same-slot volume identifies early trend "
                    "expansion rather than a low-volatility range escape."),
        expected_behaviour=("Bidirectional buffered Donchian breakouts from "
                            "09:30 through 14:45 IST."),
        known_failure_modes=("range-bound volatility spikes",
                             "exhausted wide pre-break channels",
                             "low-volume channel probes"),
        enabled=True)

    execution = ExecutionSpec(
        entry="limit_collar", slippage_collar_pct=0.001,
        stop_kind="column_atr_cap", stop_col="dc_mid", stop_atr_mult=1.25,
        hard_stop_pct=None, target_kind="r", target_r=1.5,
        partial_fraction=0.5, trail="chandelier", trail_atr_mult=2.0,
        trail_after_partial=True,
        invalidation_long_col="invalidate_long",
        invalidation_short_col="invalidate_short",
        no_progress_bars=6, no_progress_r=0.4,
        risk_per_trade_pct=0.01, max_capital_per_trade=0.20,
        intraday=True, allow_overnight=False, max_hold_bars=None)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or DonchianVolatilityExpansionParams())

    @property
    def context_symbols(self) -> tuple:
        return ("NIFTY50",)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = shared_5m_features(dataframe, p.atr_period, p.rvol_sessions)
        day = local_dates(df).dt.normalize()
        df["dc_upper"], df["dc_lower"], df["dc_mid"] = donchian_channel(
            df, p.lookback_bars, day)
        df["dc_width"] = df["dc_upper"] - df["dc_lower"]
        df["atr_mean20"] = df["atr"].rolling(
            p.atr_average_period,
            min_periods=p.atr_average_period).mean()
        df["ver"] = df["atr"] / df["atr_mean20"].replace(0.0, np.nan)
        candle_range = (df["high"] - df["low"]).replace(0.0, np.nan)
        df["close_strength_long"] = (df["close"] - df["low"]) / candle_range
        df["close_strength_short"] = (df["high"] - df["close"]) / candle_range
        df["invalidate_long"] = df["close"] < df["dc_mid"]
        df["invalidate_short"] = df["close"] > df["dc_mid"]
        return df

    def prepare_context(self, frames: dict, context: dict) -> dict:
        return add_opening_market_context(frames, context)

    def _conditions(self, df: pd.DataFrame,
                    direction: Direction) -> pd.Series:
        p = self.settings
        is_long = direction == Direction.LONG
        bound = df["dc_upper"] if is_long else df["dc_lower"]
        threshold = bound * (1.0 + (p.breakout_buffer_pct if is_long
                                    else -p.breakout_buffer_pct))
        breakout = df["close"] > threshold if is_long else df["close"] < threshold
        trend = df["ema9"] > df["ema20"] if is_long else df["ema9"] < df["ema20"]
        vwap = df["close"] > df["vwap"] if is_long else df["close"] < df["vwap"]
        rvol_floor = p.min_rvol + (0.0 if is_long else p.short_rvol_add)
        width = df["dc_width"] / df["atr"].replace(0.0, np.nan)
        window = ((df["bar_close_minute"] >= p.entry_start_minute)
                  & (df["bar_close_minute"] <= p.entry_end_minute))
        raw = (breakout & (df["ver"] >= p.min_ver)
               & (width <= p.max_channel_width_atr) & vwap & trend
               & (df["rvol"] >= rvol_floor) & (df["adt20"] >= 500_000_000)
               & window)
        if p.use_close_strength:
            strength = (df["close_strength_long"] if is_long
                        else df["close_strength_short"])
            raw &= strength >= 0.75
        if p.use_nifty_alignment:
            nifty_bound = (df["nifty_dc_upper20"] if is_long
                           else df["nifty_dc_lower20"])
            nifty = (df["nifty_close"] > nifty_bound if is_long
                     else df["nifty_close"] < nifty_bound)
            raw &= nifty_bound.isna() | nifty
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

    def entry_trigger(self, dataframe: pd.DataFrame, index: int,
                      direction: Direction) -> float:
        row = dataframe.iloc[index]
        bound = row["dc_upper"] if direction == Direction.LONG else row["dc_lower"]
        sign = 1.0 if direction == Direction.LONG else -1.0
        return float(bound) * (1.0 + sign * self.settings.breakout_buffer_pct)

    def grade_multiplier(self, confidence: float) -> float:
        return 1.0

    def signal_priority(self, dataframe: pd.DataFrame, index: int,
                        direction: Direction, confidence: float,
                        regime: float) -> float:
        row = dataframe.iloc[index]
        return 0.50 * float(row["ver"]) + 0.50 * float(row["rvol"])

    def regime_score(self, dataframe: pd.DataFrame,
                     direction: Direction) -> float:
        if dataframe.empty or "ver" not in dataframe:
            return 0.0
        return clip01((float(dataframe["ver"].iloc[-1]) - 1.0) / 0.5)

    def confidence_for(self, dataframe: pd.DataFrame,
                       direction: Direction) -> ConfidenceScore:
        if dataframe.empty or not {"ver", "rvol"}.issubset(dataframe.columns):
            return ConfidenceScore.zero("unprepared frame")
        row = dataframe.iloc[-1]
        expansion = clip01(float(row["ver"]) / 1.5)
        volume = clip01(float(row["rvol"]) / 3.0)
        score = 0.50 * expansion + 0.50 * volume
        return ConfidenceScore(
            score=score,
            components={
                "volatility_expansion": {"score": expansion, "weight": 0.50,
                                           "detail": f"VER {row['ver']:.2f}"},
                "breakout_rvol": {"score": volume, "weight": 0.50,
                                   "detail": f"RVOL {row['rvol']:.2f}"}},
            reason=f"STRAT-11 {direction.value} Donchian expansion")

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if dataframe.empty or "dc_mid" not in dataframe:
            return ConfidenceScore.zero("unprepared frame")
        direction = (Direction.LONG if dataframe["close"].iloc[-1]
                     >= dataframe["dc_mid"].iloc[-1] else Direction.SHORT)
        return self.confidence_for(dataframe, direction)
