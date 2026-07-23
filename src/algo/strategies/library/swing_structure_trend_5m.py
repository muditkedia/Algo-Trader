"""STRAT-12 - Swing Structure Trend Continuation (SSTC), 5-minute."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import confirmed_fractal_pivots, swing_structure_bias
from algo.execution import ExecutionSpec
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import ConfidenceScore, clip01
from algo.strategies.opening_context import (
    add_opening_market_context, completed_15m_structure, local_dates,
    shared_5m_features,
)


@dataclass(frozen=True)
class SwingStructureTrendParams:
    pivot_k: int = 2
    breakout_buffer_pct: float = 0.0010
    retest_tolerance_pct: float = 0.0015
    max_retest_depth_atr: float = 0.35
    max_retest_bars: int = 10
    min_rvol_trigger: float = 1.60
    short_rvol_add: float = 0.40
    rvol_sessions: int = 10
    atr_period: int = 14
    stop_buffer_atr: float = 0.10
    tp1_r: float = 1.50
    tp1_fraction: float = 0.50
    chandelier_atr: float = 2.00
    risk_per_trade_pct: float = 0.01
    max_capital_per_trade: float = 0.20
    slippage_collar_pct: float = 0.001
    no_progress_bars: int = 8
    no_progress_r: float = 0.40
    entry_start_minute: int = 9 * 60 + 30
    entry_end_minute: int = 14 * 60 + 45
    use_15m_structure: bool = True
    use_pullback_volume: bool = True
    use_nifty_structure: bool = True
    max_pullback_volume_ratio: float = 0.65

    @classmethod
    def from_dict(cls, data) -> "SwingStructureTrendParams":
        return from_dict(cls, data)


class SwingStructureTrendContinuation(StrategyProfile):

    meta = StrategyMeta(
        name="swing_structure_trend_5m", version="2.0.0",
        spec_id="STRAT-12", direction=Direction.BOTH,
        holding_scope=HoldingScope.INTRADAY, timeframe="5m", min_bars=1505,
        blocked_by_active_groups=("orb_retest_owner",),
        required_columns=(
            "close", "atr", "ema9", "ema20", "vwap", "rvol", "adt20",
            "structure_bias", "structure_15m", "nifty_structure",
            "trigger_base_long", "trigger_base_short", "swing_stop_long",
            "swing_stop_short", "swing_level_long", "swing_level_short",
            "bar_close_minute"),
        supported_regimes=("intraday_trend", "orderly_pullback"),
        hypothesis=("A confirmed HH/HL or LH/LL sequence followed by a "
                    "buffered break, shallow role-reversal retest and volume "
                    "resumption continues the established auction trend."),
        expected_behaviour=("One causal fractal-break/retest continuation per "
                            "directional structure and session."),
        known_failure_modes=("overlapping pivots in rotational markets",
                             "deep retests through the broken swing",
                             "low-volume reversal bars"), enabled=True)

    execution = ExecutionSpec(
        entry="limit_collar", slippage_collar_pct=0.001,
        stop_kind="column", stop_long_col="swing_stop_long",
        stop_short_col="swing_stop_short", hard_stop_pct=None,
        target_kind="r", target_r=1.5, partial_fraction=0.5,
        trail="chandelier", trail_atr_mult=2.0, trail_after_partial=True,
        invalidation_long_col="invalidate_long",
        invalidation_short_col="invalidate_short",
        no_progress_bars=8, no_progress_r=0.4,
        risk_per_trade_pct=0.01, max_capital_per_trade=0.20,
        intraday=True, allow_overnight=False, max_hold_bars=None)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or SwingStructureTrendParams())

    @property
    def context_symbols(self) -> tuple:
        return ("NIFTY50",)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = shared_5m_features(dataframe, p.atr_period, p.rvol_sessions)
        day = local_dates(df).dt.normalize()
        df["pivot_high"], df["pivot_low"] = confirmed_fractal_pivots(
            df, p.pivot_k, day)
        df["structure_bias"] = swing_structure_bias(
            df, p.pivot_k, day, pivot_high=df["pivot_high"],
            pivot_low=df["pivot_low"])
        structure15 = completed_15m_structure(df, p.pivot_k)
        if structure15.empty:
            df["structure_15m"] = np.nan
        else:
            df = pd.merge_asof(df.sort_values("date"), structure15,
                               on="date", direction="backward")
        self._track_retests(df)
        df["invalidate_long"] = ((df["close"] < df["swing_level_long"])
                                 & (df["close"] < df["vwap"]))
        df["invalidate_short"] = ((df["close"] > df["swing_level_short"])
                                  & (df["close"] > df["vwap"]))
        return df

    def _track_retests(self, df: pd.DataFrame) -> None:
        p, n = self.settings, len(df)
        float_names = ("swing_level", "pullback_pivot", "pullback_avg_volume",
                       "breakout_volume", "retest_bars", "impulse_atr",
                       "swing_stop")
        bool_names = ("retest_touched", "trigger_attempt", "trigger_base",
                      "depth_failure", "timeout_failure")
        output = {
            side: {**{name: np.full(n, np.nan) for name in float_names},
                   **{name: np.zeros(n, dtype=bool) for name in bool_names}}
            for side in ("long", "short")}
        high = df["high"].to_numpy(float)
        low = df["low"].to_numpy(float)
        opened = df["open"].to_numpy(float)
        close = df["close"].to_numpy(float)
        volume = df["volume"].to_numpy(float)
        atr = df["atr"].to_numpy(float)
        ph = df["pivot_high"].to_numpy(float)
        pl = df["pivot_low"].to_numpy(float)
        bias = df["structure_bias"].to_numpy(float)
        days = local_dates(df).dt.normalize().to_numpy()

        for session_day in pd.unique(days):
            positions = np.flatnonzero(days == session_day)
            states = {
                "long": {"active": False, "managing": False,
                         "last_level": np.nan},
                "short": {"active": False, "managing": False,
                          "last_level": np.nan},
            }
            for pos in positions:
                if np.isfinite(ph[pos]):
                    states["long"]["last_level"] = ph[pos]
                if np.isfinite(pl[pos]):
                    states["short"]["last_level"] = pl[pos]
                for side, sign in (("long", 1.0), ("short", -1.0)):
                    state, out = states[side], output[side]
                    if state["managing"]:
                        out["swing_level"][pos] = state["level"]
                        continue
                    level = state["last_level"]
                    if not state["active"]:
                        threshold = level * (1.0 + sign * p.breakout_buffer_pct)
                        broke = (close[pos] > threshold if sign > 0
                                 else close[pos] < threshold)
                        if (np.isfinite(level) and bias[pos] == sign and broke):
                            state.update(active=True, level=level, break_pos=pos,
                                         break_atr=atr[pos],
                                         break_volume=volume[pos],
                                         extreme=high[pos] if sign > 0 else low[pos],
                                         pivot=np.nan, touched=False,
                                         volume_sum=0.0, volume_count=0)
                        continue

                    out["swing_level"][pos] = state["level"]
                    bars = pos - state["break_pos"]
                    if bars > p.max_retest_bars:
                        out["timeout_failure"][pos] = True
                        state["active"] = False
                        continue
                    level = state["level"]
                    state["extreme"] = (max(state["extreme"], high[pos])
                                        if sign > 0 else
                                        min(state["extreme"], low[pos]))
                    impulse = sign * (state["extreme"] - level)
                    out["impulse_atr"][pos] = impulse / state["break_atr"] \
                        if state["break_atr"] > 0 else np.nan
                    depth = level - sign * p.max_retest_depth_atr * atr[pos]
                    violated = close[pos] < depth if sign > 0 else close[pos] > depth
                    if violated:
                        out["depth_failure"][pos] = True
                        state["active"] = False
                        continue
                    zone = level * (1.0 + sign * p.retest_tolerance_pct)
                    touched = low[pos] <= zone if sign > 0 else high[pos] >= zone
                    state["touched"] = state["touched"] or touched
                    pivot = low[pos] if sign > 0 else high[pos]
                    state["pivot"] = (pivot if not np.isfinite(state["pivot"])
                                      else (min(state["pivot"], pivot)
                                            if sign > 0 else
                                            max(state["pivot"], pivot)))
                    avg = (state["volume_sum"] / state["volume_count"]
                           if state["volume_count"] else np.nan)
                    directional = close[pos] > opened[pos] if sign > 0 \
                        else close[pos] < opened[pos]
                    resumed = close[pos] > high[pos - 1] if sign > 0 \
                        else close[pos] < low[pos - 1]
                    held = close[pos] > level if sign > 0 else close[pos] < level
                    attempt = bool(state["touched"] and directional
                                   and resumed and held)
                    out["pullback_pivot"][pos] = state["pivot"]
                    out["pullback_avg_volume"][pos] = avg
                    out["breakout_volume"][pos] = state["break_volume"]
                    out["retest_bars"][pos] = bars
                    out["retest_touched"][pos] = state["touched"]
                    out["trigger_attempt"][pos] = attempt
                    out["trigger_base"][pos] = attempt
                    structural = (state["pivot"] - p.stop_buffer_atr * atr[pos]
                                  if sign > 0 else
                                  state["pivot"] + p.stop_buffer_atr * atr[pos])
                    level_stop = level - sign * p.max_retest_depth_atr * atr[pos]
                    out["swing_stop"][pos] = (min(structural, level_stop)
                                               if sign > 0 else
                                               max(structural, level_stop))
                    if attempt:
                        state.update(active=False, managing=True)
                    else:
                        state["volume_sum"] += volume[pos]
                        state["volume_count"] += 1

        for side in ("long", "short"):
            for name in float_names + bool_names:
                df[f"{name}_{side}"] = output[side][name]

    def prepare_context(self, frames: dict, context: dict) -> dict:
        return add_opening_market_context(frames, context)

    def _conditions(self, df: pd.DataFrame,
                    direction: Direction) -> pd.Series:
        p = self.settings
        is_long = direction == Direction.LONG
        side = direction.value
        sign = 1.0 if is_long else -1.0
        trend = df["ema9"] > df["ema20"] if is_long else df["ema9"] < df["ema20"]
        vwap = df["close"] > df["vwap"] if is_long else df["close"] < df["vwap"]
        rvol_floor = p.min_rvol_trigger + (0.0 if is_long else p.short_rvol_add)
        window = ((df["bar_close_minute"] >= p.entry_start_minute)
                  & (df["bar_close_minute"] <= p.entry_end_minute))
        raw = (df[f"trigger_base_{side}"] & trend & vwap
               & (df["rvol"] >= rvol_floor) & (df["adt20"] >= 500_000_000)
               & window)
        if p.use_pullback_volume:
            ratio = (df[f"pullback_avg_volume_{side}"]
                     / df[f"breakout_volume_{side}"].replace(0.0, np.nan))
            raw &= ratio < p.max_pullback_volume_ratio
        if p.use_15m_structure:
            raw &= df["structure_15m"].isna() | (df["structure_15m"] == sign)
        if p.use_nifty_structure:
            raw &= df["nifty_structure"].isna() | (df["nifty_structure"] == sign)
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
        row, previous = dataframe.iloc[index], dataframe.iloc[index - 1]
        level = row[f"swing_level_{direction.value}"]
        return (max(float(level), float(previous["high"]))
                if direction == Direction.LONG else
                min(float(level), float(previous["low"])))

    def grade_multiplier(self, confidence: float) -> float:
        return 1.0

    def signal_priority(self, dataframe: pd.DataFrame, index: int,
                        direction: Direction, confidence: float,
                        regime: float) -> float:
        row = dataframe.iloc[index]
        return (0.50 * float(row["rvol"])
                + 0.50 * float(row[f"impulse_atr_{direction.value}"]))

    def confidence_for(self, dataframe: pd.DataFrame,
                       direction: Direction) -> ConfidenceScore:
        needed = {"rvol", f"impulse_atr_{direction.value}"}
        if dataframe.empty or not needed.issubset(dataframe.columns):
            return ConfidenceScore.zero("unprepared frame")
        row, side = dataframe.iloc[-1], direction.value
        volume = clip01(float(row["rvol"]) / 3.0)
        impulse = clip01(float(row[f"impulse_atr_{side}"]) / 2.0)
        return ConfidenceScore(
            score=0.50 * volume + 0.50 * impulse,
            components={
                "trigger_rvol": {"score": volume, "weight": 0.50,
                                  "detail": f"RVOL {row['rvol']:.2f}"},
                "impulse_extension": {"score": impulse, "weight": 0.50,
                                      "detail": f"{row[f'impulse_atr_{side}']:.2f} ATR"}},
            reason=f"STRAT-12 {side} swing retest")

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if dataframe.empty or "structure_bias" not in dataframe:
            return ConfidenceScore.zero("unprepared frame")
        direction = (Direction.LONG if dataframe["structure_bias"].iloc[-1] >= 0
                     else Direction.SHORT)
        return self.confidence_for(dataframe, direction)
