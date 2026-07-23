"""STRAT-13 - Volatility Contraction Pattern (VCP), 5-minute."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import confirmed_fractal_pivots
from algo.execution import ExecutionSpec
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import ConfidenceScore, clip01
from algo.strategies.opening_context import (
    add_opening_market_context, local_dates, shared_5m_features,
)


@dataclass(frozen=True)
class VolatilityContractionParams:
    pivot_k: int = 2
    min_contractions: int = 2
    max_cdr: float = 0.60
    max_d1_depth_pct: float = 0.040
    max_hvr: float = 0.50
    handle_volume_bars: int = 3
    volume_sma_bars: int = 20
    breakout_buffer_pct: float = 0.0010
    min_rvol: float = 2.00
    short_rvol_add: float = 0.50
    min_body_range_ratio: float = 0.50
    rvol_sessions: int = 10
    atr_period: int = 14
    stop_buffer_atr: float = 0.10
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

    @classmethod
    def from_dict(cls, data) -> "VolatilityContractionParams":
        return from_dict(cls, data)


class VolatilityContractionPattern(StrategyProfile):

    meta = StrategyMeta(
        name="volatility_contraction_5m", version="2.0.0",
        spec_id="STRAT-13", direction=Direction.BOTH,
        holding_scope=HoldingScope.INTRADAY, timeframe="5m", min_bars=1505,
        simultaneous_priority_over=("donchian_volatility_expansion_5m",),
        required_columns=(
            "close", "atr", "ema9", "ema20", "vwap", "rvol", "adt20",
            "hvr", "pattern_valid_long", "pattern_valid_short",
            "pattern_level_long", "pattern_level_short", "vcp_stop_long",
            "vcp_stop_short", "wave_count_long", "wave_count_short",
            "nifty_close", "nifty_dc_upper10", "nifty_dc_lower10",
            "bar_close_minute"),
        supported_regimes=("volatility_contraction", "breakout_expansion"),
        hypothesis=("Two or more causal fractal contraction waves with "
                    "declining depth and handle volume reveal supply "
                    "absorption before a high-RVOL expansion break."),
        expected_behaviour=("One bidirectional VCP breakout attempt per "
                            "completed contraction sequence."),
        known_failure_modes=("megaphone expansion patterns",
                             "distributional handle volume",
                             "premature pivot breaks"), enabled=True)

    execution = ExecutionSpec(
        entry="limit_collar", slippage_collar_pct=0.001,
        stop_kind="column_atr_cap", stop_long_col="vcp_stop_long",
        stop_short_col="vcp_stop_short", stop_atr_mult=1.25,
        hard_stop_pct=None, target_kind="r", target_r=1.5,
        partial_fraction=0.5, trail="chandelier", trail_atr_mult=2.0,
        trail_after_partial=True,
        invalidation_long_col="invalidate_long",
        invalidation_short_col="invalidate_short",
        no_progress_bars=6, no_progress_r=0.4,
        risk_per_trade_pct=0.01, max_capital_per_trade=0.20,
        intraday=True, allow_overnight=False, max_hold_bars=None)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or VolatilityContractionParams())

    @property
    def context_symbols(self) -> tuple:
        return ("NIFTY50",)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = shared_5m_features(dataframe, p.atr_period, p.rvol_sessions)
        day = local_dates(df).dt.normalize()
        df["pivot_high"], df["pivot_low"] = confirmed_fractal_pivots(
            df, p.pivot_k, day)
        volume_mean = df["volume"].rolling(
            p.volume_sma_bars, min_periods=p.volume_sma_bars).mean()
        handle_mean = df["volume"].shift(1).rolling(
            p.handle_volume_bars,
            min_periods=p.handle_volume_bars).mean()
        df["hvr"] = handle_mean / volume_mean.replace(0.0, np.nan)
        candle_range = (df["high"] - df["low"]).replace(0.0, np.nan)
        df["body_range_ratio"] = (
            (df["close"] - df["open"]).abs() / candle_range)
        self._track_patterns(df)
        df["invalidate_long"] = ((df["close"] < df["pattern_level_long"])
                                 & (df["close"] < df["vwap"]))
        df["invalidate_short"] = ((df["close"] > df["pattern_level_short"])
                                  & (df["close"] > df["vwap"]))
        return df

    def _track_patterns(self, df: pd.DataFrame) -> None:
        p, n = self.settings, len(df)
        float_names = ("pattern_level", "handle_pivot", "d1", "d2", "cdr",
                       "wave_count", "vcp_stop")
        bool_names = ("pattern_ready", "break_attempt", "pattern_valid")
        output = {
            side: {**{name: np.full(n, np.nan) for name in float_names},
                   **{name: np.zeros(n, dtype=bool) for name in bool_names}}
            for side in ("long", "short")}
        ph = df["pivot_high"].to_numpy(float)
        pl = df["pivot_low"].to_numpy(float)
        close = df["close"].to_numpy(float)
        atr = df["atr"].to_numpy(float)
        days = local_dates(df).dt.normalize().to_numpy()

        for session_day in pd.unique(days):
            positions = np.flatnonzero(days == session_day)
            states = {
                "long": {"pending": None, "waves": [], "managing": False},
                "short": {"pending": None, "waves": [], "managing": False},
            }
            for pos in positions:
                for side, sign in (("long", 1.0), ("short", -1.0)):
                    state, out = states[side], output[side]
                    first_pivot = ph[pos] if sign > 0 else pl[pos]
                    second_pivot = pl[pos] if sign > 0 else ph[pos]
                    if np.isfinite(first_pivot):
                        state["pending"] = (float(first_pivot), pos)
                    if np.isfinite(second_pivot) and state["pending"] is not None:
                        first, first_pos = state["pending"]
                        if first_pos < pos:
                            depth = (first - second_pivot) / first if sign > 0 \
                                else (second_pivot - first) / first
                            if depth > 0:
                                state["waves"].append(
                                    (first, float(second_pivot), float(depth)))
                                state["waves"] = state["waves"][-3:]
                            state["pending"] = None

                    if state["managing"]:
                        out["pattern_level"][pos] = state["level"]
                        continue
                    waves = state["waves"]
                    if len(waves) < p.min_contractions:
                        continue
                    last_two = waves[-2:]
                    d1, d2 = last_two[0][2], last_two[1][2]
                    valid = (d1 <= p.max_d1_depth_pct
                             and d2 <= p.max_cdr * d1)
                    if not valid:
                        continue
                    used = last_two
                    if (len(waves) >= 3 and waves[-3][2] <= p.max_d1_depth_pct
                            and waves[-2][2] <= p.max_cdr * waves[-3][2]
                            and waves[-1][2] <= p.max_cdr * waves[-2][2]):
                        used = waves[-3:]
                    level = (max(w[0] for w in used) if sign > 0
                             else min(w[0] for w in used))
                    handle = used[-1][1]
                    first_invalidation = used[0][1]
                    invalid = (close[pos] < first_invalidation if sign > 0
                               else close[pos] > first_invalidation)
                    if invalid:
                        state["waves"] = []
                        state["pending"] = None
                        continue
                    out["pattern_level"][pos] = level
                    out["handle_pivot"][pos] = handle
                    out["d1"][pos], out["d2"][pos] = d1, d2
                    out["cdr"][pos] = d2 / d1
                    out["wave_count"][pos] = len(used)
                    out["pattern_ready"][pos] = True
                    threshold = level * (1.0 + sign * p.breakout_buffer_pct)
                    attempt = close[pos] > threshold if sign > 0 \
                        else close[pos] < threshold
                    premature = close[pos] > level if sign > 0 else close[pos] < level
                    if attempt:
                        out["break_attempt"][pos] = True
                        out["pattern_valid"][pos] = True
                        structural = (handle - p.stop_buffer_atr * atr[pos]
                                      if sign > 0 else
                                      handle + p.stop_buffer_atr * atr[pos])
                        out["vcp_stop"][pos] = structural
                        state.update(managing=True, level=level, waves=[])
                    elif premature:
                        state["waves"] = []
                        state["pending"] = None

        for side in ("long", "short"):
            for name in float_names + bool_names:
                df[f"{name}_{side}"] = output[side][name]

    def prepare_context(self, frames: dict, context: dict) -> dict:
        return add_opening_market_context(frames, context)

    def _conditions(self, df: pd.DataFrame,
                    direction: Direction) -> pd.Series:
        p = self.settings
        is_long, side = direction == Direction.LONG, direction.value
        trend = df["ema9"] > df["ema20"] if is_long else df["ema9"] < df["ema20"]
        vwap = df["close"] > df["vwap"] if is_long else df["close"] < df["vwap"]
        candle = df["close"] > df["open"] if is_long else df["close"] < df["open"]
        rvol_floor = p.min_rvol + (0.0 if is_long else p.short_rvol_add)
        window = ((df["bar_close_minute"] >= p.entry_start_minute)
                  & (df["bar_close_minute"] <= p.entry_end_minute))
        raw = (df[f"pattern_valid_{side}"] & (df["hvr"] <= p.max_hvr)
               & trend & vwap & candle
               & (df["body_range_ratio"] >= p.min_body_range_ratio)
               & (df["rvol"] >= rvol_floor) & (df["adt20"] >= 500_000_000)
               & window)
        if p.use_nifty_alignment:
            bound = df["nifty_dc_upper10"] if is_long else df["nifty_dc_lower10"]
            aligned = df["nifty_close"] > bound if is_long else df["nifty_close"] < bound
            raw &= bound.isna() | aligned
        return raw.fillna(False)

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
        level = float(dataframe[f"pattern_level_{direction.value}"].iloc[index])
        sign = 1.0 if direction == Direction.LONG else -1.0
        return level * (1.0 + sign * self.settings.breakout_buffer_pct)

    def grade_multiplier(self, confidence: float) -> float:
        return 1.0

    def signal_priority(self, dataframe: pd.DataFrame, index: int,
                        direction: Direction, confidence: float,
                        regime: float) -> float:
        row = dataframe.iloc[index]
        hvr = max(float(row["hvr"]), 1e-12)
        return 0.50 * float(row["rvol"]) + 0.50 / hvr

    def confidence_for(self, dataframe: pd.DataFrame,
                       direction: Direction) -> ConfidenceScore:
        needed = {"rvol", "hvr", f"wave_count_{direction.value}"}
        if dataframe.empty or not needed.issubset(dataframe.columns):
            return ConfidenceScore.zero("unprepared frame")
        row, side = dataframe.iloc[-1], direction.value
        volume = clip01(float(row["rvol"]) / 3.5)
        dryness = clip01(1.0 - float(row["hvr"]))
        wave_count = float(row[f"wave_count_{side}"])
        wave_quality = 1.0 if wave_count >= 3 else 0.75
        score = 0.45 * volume + 0.45 * dryness + 0.10 * wave_quality
        return ConfidenceScore(
            score=score,
            components={
                "breakout_rvol": {"score": volume, "weight": 0.45,
                                   "detail": f"RVOL {row['rvol']:.2f}"},
                "handle_dryness": {"score": dryness, "weight": 0.45,
                                   "detail": f"HVR {row['hvr']:.2f}"},
                "wave_preference": {"score": wave_quality, "weight": 0.10,
                                    "detail": f"{wave_count:.0f} waves"}},
            reason=f"STRAT-13 {side} VCP breakout")

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if dataframe.empty:
            return ConfidenceScore.zero("empty frame")
        direction = (Direction.LONG if dataframe["close"].iloc[-1]
                     >= dataframe["open"].iloc[-1] else Direction.SHORT)
        return self.confidence_for(dataframe, direction)
