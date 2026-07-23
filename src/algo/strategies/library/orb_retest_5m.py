"""STRAT-02 - canonical 5-minute ORB Retest Continuation.

This replaces the retired long-only ``first_pullback_15m`` approximation. The
state machine is reconstructed deterministically from completed session bars,
so a restart restores the same breakout/retest state without mutable strategy
state or a second persistence store.

Historical spread and point-in-time sector membership are unavailable. As in
STRAT-01, liquidity receives at most the conservative five-point ADT score and
the live top-500 scan universe supplies the documented breadth proxy.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import (
    atr, ema, macd_histogram, opening_range, roc, session_vwap,
    slot_relative_volume,
)
from algo.execution import ExecutionSpec
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import ConfidenceScore, clip01
from algo.strategies.opening_context import (
    add_opening_market_context, completed_15m_adx, local_dates,
    shared_5m_features,
)


@dataclass(frozen=True)
class OrbRetestParams:
    range_minutes: int = 5
    breakout_buffer_pct: float = 0.001
    min_breakout_ext_atr: float = 0.50
    retest_tolerance_pct: float = 0.0015
    max_retest_depth_atr: float = 0.50
    max_retest_bars: int = 8
    min_rvol_trigger: float = 1.75
    short_rvol_add: float = 0.50
    rvol_sessions: int = 10
    atr_period: int = 14
    min_regime_score: float = 42.0
    min_confidence_score: float = 55.0
    risk_per_trade_pct: float = 0.01
    max_capital_per_trade: float = 0.20
    stop_buffer_atr: float = 0.20
    max_stop_atr: float = 1.25
    tp1_r: float = 1.50
    grade_c_tp1_r: float = 1.00
    tp1_fraction: float = 0.50
    chandelier_atr: float = 2.00
    slippage_collar_pct: float = 0.001
    no_progress_bars: int = 8
    no_progress_r: float = 0.50
    entry_start_minute: int = 9 * 60 + 30
    entry_end_minute: int = 14 * 60 + 45

    @classmethod
    def from_dict(cls, data) -> "OrbRetestParams":
        return from_dict(cls, data)


class OpeningRangeRetest(StrategyProfile):

    meta = StrategyMeta(
        name="orb_retest_5m", version="2.0.0", spec_id="STRAT-02",
        active_conflict_group="opening_or_retest", direction=Direction.BOTH,
        active_block_group="orb_retest_owner",
        simultaneous_priority_over=("swing_structure_trend_5m",),
        blocked_by_session_groups=("opening_drive", "gap_go"),
        holding_scope=HoldingScope.INTRADAY, timeframe="5m", min_bars=1505,
        required_columns=(
            "close", "or_high", "or_low", "atr", "vwap", "rvol", "adt20",
            "natr20", "trigger_base_long", "trigger_base_short",
            "retest_stop_long", "retest_stop_short", "regime_long",
            "regime_short", "confidence_long", "confidence_short"),
        supported_regimes=("trend",),
        hypothesis=("A sufficiently extended opening-range break that retests "
                    "and holds the broken boundary creates a lower-slippage "
                    "continuation entry on renewed participation."),
        expected_behaviour=("At most one confirmed OR-boundary retest entry "
                            "per symbol/session, long or short."),
        known_failure_modes=(
            "deep closes back inside the opening range",
            "retests lasting more than eight completed bars",
            "high-volume distribution during the pullback",
        ), enabled=True)

    execution = ExecutionSpec(
        entry="limit_collar", slippage_collar_pct=0.001,
        stop_kind="column_atr_cap", stop_long_col="retest_stop_long",
        stop_short_col="retest_stop_short", stop_atr_mult=1.25,
        hard_stop_pct=None, target_kind="r", target_r=1.5,
        target_r_long_col="target_r_long",
        target_r_short_col="target_r_short",
        partial_fraction=0.5, trail="chandelier", trail_atr_mult=2.0,
        trail_after_partial=True,
        invalidation_long_col="invalidate_long",
        invalidation_short_col="invalidate_short",
        no_progress_bars=8, no_progress_r=0.5,
        risk_per_trade_pct=0.01, max_capital_per_trade=0.20,
        intraday=True, allow_overnight=False, max_hold_bars=None)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or OrbRetestParams())

    @property
    def context_symbols(self) -> tuple:
        return ("NIFTY50",)

    def min_history(self) -> int:
        return int(self.meta.min_bars)

    def priority_score(self, confidence: float, regime: float) -> float:
        return 0.65 * confidence + 0.35 * regime

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = shared_5m_features(dataframe, p.atr_period, p.rvol_sessions)
        or_high, or_low, after = opening_range(df, p.range_minutes)
        df["or_high"], df["or_low"], df["after_range"] = or_high, or_low, after
        df["atr_mean20"] = df["atr"].rolling(20, min_periods=20).mean()
        df["roc20"] = roc(df["close"], 20)
        df["macd_hist"] = macd_histogram(df["close"])

        adx15 = completed_15m_adx(df)
        if not adx15.empty:
            df = pd.merge_asof(df.sort_values("date"), adx15,
                               on="date", direction="backward")
        else:
            df["adx15"] = np.nan
        self._track_states(df)
        df["target_r_long"] = p.tp1_r
        df["target_r_short"] = p.tp1_r
        df["invalidate_long"] = ((df["close"] < df["or_high"])
                                 & (df["close"] < df["vwap"]))
        df["invalidate_short"] = ((df["close"] > df["or_low"])
                                  & (df["close"] > df["vwap"]))
        return df

    def _track_states(self, df: pd.DataFrame) -> None:
        """Reconstruct both deterministic breakout/retest state machines.

        The state transitions are intentionally identical to the original
        implementation.  NumPy buffers replace thousands of ``df.loc`` /
        ``df.at`` scalar operations and are assigned once at the end; this was
        the largest strategy-local cost in the measured 5-minute scan.
        """
        p = self.settings
        n = len(df)
        float_cols = [
            "wave_extension", "retest_pivot", "breakout_volume",
            "pullback_avg_volume", "retest_bars", "retest_stop"]
        bool_cols = ["breakout_state", "retest_touched", "level_hold",
                     "trigger_base", "trigger_attempt", "depth_failure",
                     "timeout_failure"]
        values = {
            suffix: {
                **{col: np.full(n, np.nan, dtype=float)
                   for col in float_cols},
                **{col: np.zeros(n, dtype=bool) for col in bool_cols},
            } for suffix in ("long", "short")
        }
        opened = df["open"].to_numpy(dtype=float)
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        close = df["close"].to_numpy(dtype=float)
        volume = df["volume"].to_numpy(dtype=float)
        atr_values = df["atr"].to_numpy(dtype=float)
        after = df["after_range"].to_numpy(dtype=bool)
        or_high = df["or_high"].to_numpy(dtype=float)
        or_low = df["or_low"].to_numpy(dtype=float)

        day = local_dates(df).dt.normalize().to_numpy()
        for session_day in pd.unique(day):
            positions = np.flatnonzero(day == session_day)
            states = {
                "long": {"active": False, "done": False},
                "short": {"active": False, "done": False},
            }
            for pos in positions:
                if not after[pos] or not np.isfinite(atr_values[pos]):
                    continue
                for suffix, sign in (("long", 1.0), ("short", -1.0)):
                    state = states[suffix]
                    out = values[suffix]
                    boundary = or_high[pos] if sign > 0 else or_low[pos]
                    threshold = boundary * (1 + sign * p.breakout_buffer_pct)
                    broke = (close[pos] > threshold if sign > 0
                             else close[pos] < threshold)
                    if not state["active"]:
                        if not state["done"] and broke:
                            state.update(
                                active=True, break_pos=pos,
                                break_atr=atr_values[pos],
                                break_volume=volume[pos],
                                extreme=high[pos] if sign > 0 else low[pos],
                                pivot=np.nan, touched=False,
                                volume_sum=0.0, volume_count=0)
                            out["breakout_state"][pos] = True
                        continue

                    bars = pos - state["break_pos"]
                    if bars > p.max_retest_bars:
                        out["timeout_failure"][pos] = True
                        state.update(active=False, done=True)
                        continue
                    state["extreme"] = (max(state["extreme"], high[pos])
                                        if sign > 0 else
                                        min(state["extreme"], low[pos]))
                    extension = sign * (state["extreme"] - boundary)
                    depth_limit = (boundary - p.max_retest_depth_atr * atr_values[pos]
                                   if sign > 0 else
                                   boundary + p.max_retest_depth_atr * atr_values[pos])
                    violated = (close[pos] < depth_limit if sign > 0
                                else close[pos] > depth_limit)
                    if violated:
                        out["depth_failure"][pos] = True
                        state.update(active=False, done=True)
                        continue

                    touched_before = state["touched"]
                    zone = boundary * (1 + sign * p.retest_tolerance_pct)
                    touched_now = (low[pos] <= zone if sign > 0
                                   else high[pos] >= zone)
                    if touched_now:
                        state["touched"] = True
                    state["pivot"] = (
                        low[pos] if sign > 0 else high[pos]
                        if not np.isfinite(state["pivot"]) else
                        (min(state["pivot"], low[pos]) if sign > 0
                         else max(state["pivot"], high[pos])))
                    pullback_avg = (state["volume_sum"] / state["volume_count"]
                                    if state["volume_count"] else np.nan)
                    candle_direction = (close[pos] > opened[pos] if sign > 0
                                        else close[pos] < opened[pos])
                    resumed = (close[pos] > high[pos - 1] if sign > 0
                               else close[pos] < low[pos - 1])
                    attempt = bool(touched_before and candle_direction and resumed)
                    extended = extension >= p.min_breakout_ext_atr * state["break_atr"]

                    out["breakout_state"][pos] = True
                    out["wave_extension"][pos] = extension
                    out["retest_pivot"][pos] = state["pivot"]
                    out["breakout_volume"][pos] = state["break_volume"]
                    out["pullback_avg_volume"][pos] = pullback_avg
                    out["retest_bars"][pos] = bars
                    out["retest_touched"][pos] = state["touched"]
                    out["level_hold"][pos] = True
                    out["trigger_attempt"][pos] = attempt
                    out["trigger_base"][pos] = attempt and extended
                    stop = (state["pivot"] - p.stop_buffer_atr * atr_values[pos]
                            if sign > 0 else
                            state["pivot"] + p.stop_buffer_atr * atr_values[pos])
                    out["retest_stop"][pos] = stop

                    if state["touched"] and not attempt:
                        state["volume_sum"] += volume[pos]
                        state["volume_count"] += 1

        for suffix in ("long", "short"):
            for col in float_cols:
                df[f"{col}_{suffix}"] = values[suffix][col]
            for col in bool_cols:
                df[f"{col}_{suffix}"] = values[suffix][col]

    def prepare_context(self, frames: dict, context: dict) -> dict:
        add_opening_market_context(frames, context)
        for df in frames.values():
            self._add_scores(df)
        return frames

    def _add_scores(self, df: pd.DataFrame) -> None:
        p = self.settings
        atr_ratio = df["atr"] / df["atr_mean20"].replace(0.0, np.nan)
        gap = (df["open"] / df["prior_close"] - 1.0).abs()
        for direction in (Direction.LONG, Direction.SHORT):
            suffix = direction.value
            sign = 1.0 if direction == Direction.LONG else -1.0
            trend_aligned = ((df["ema9"] > df["ema20"])
                             if sign > 0 else (df["ema9"] < df["ema20"]))
            nifty_aligned = df["nifty_trend"] == sign
            s1 = np.select([
                (df["adx15"] >= 22) & trend_aligned & nifty_aligned,
                (df["adx15"] >= 15) & (df["adx15"] < 22),
            ], [10.0, 5.0], default=0.0)
            directional_roc = sign * df["roc20"]
            directional_macd = sign * df["macd_hist"]
            s2 = np.select([
                (directional_roc >= 0.010) & (directional_macd > 0),
                (directional_roc >= 0.003) & (directional_roc < 0.010),
            ], [10.0, 5.0], default=0.0)
            s3 = np.select([atr_ratio >= 1.15,
                            (atr_ratio >= 0.90) & (atr_ratio < 1.15)],
                           [10.0, 5.0], default=0.0)
            # Spread history is unavailable: never award the 10-point branch.
            s4 = np.where(df["adt20"] >= 500_000_000, 5.0, 0.0)
            ratio = (df["ad_ratio"] if sign > 0 else
                     (1.0 / df["ad_ratio"].replace(0.0, np.nan)).where(
                         df["ad_ratio"] != 0.0, np.inf))
            breadth = (df["breadth_above_vwap"] if sign > 0
                       else 1.0 - df["breadth_above_vwap"])
            s5 = np.select([(ratio >= 1.8) & (breadth > 0.60),
                            (ratio >= 1.1) & (ratio < 1.8)],
                           [10.0, 5.0], default=0.0)
            s6 = np.select([(gap >= 0.003) & (gap <= 0.015), gap < 0.003],
                           [10.0, 5.0], default=0.0)
            pullback_ratio = (df[f"pullback_avg_volume_{suffix}"]
                              / df[f"breakout_volume_{suffix}"].replace(
                                  0.0, np.nan))
            s7 = np.select([pullback_ratio < 0.70,
                            (pullback_ratio >= 0.70) & (pullback_ratio <= 1.0)],
                           [10.0, 5.0], default=0.0)
            regime = s1 + s2 + s3 + s4 + s5 + s6 + s7
            df[f"regime_{suffix}"] = regime

            boundary = df["or_high"] if sign > 0 else df["or_low"]
            precision = ((df[f"retest_pivot_{suffix}"] / boundary - 1.0)
                         .abs())
            c_level = np.select([precision <= 0.001, precision <= 0.0025],
                                [20.0, 10.0], default=0.0)
            vwap_distance = (df["vwap"] / boundary - 1.0) * sign
            c_vwap = np.select([vwap_distance.abs() <= 0.002,
                                (vwap_distance <= 0)
                                & (vwap_distance >= -0.005)],
                               [20.0, 10.0], default=0.0)
            previous = df.shift(1)
            body_engulf = (((df["open"] <= previous["close"])
                            & (df["close"] >= previous["open"])) if sign > 0
                           else ((df["open"] >= previous["close"])
                                 & (df["close"] <= previous["open"])))
            candle_range = (df["high"] - df["low"]).replace(0.0, np.nan)
            wick = ((np.minimum(df["open"], df["close"]) - df["low"])
                    if sign > 0 else
                    (df["high"] - np.maximum(df["open"], df["close"])))
            pinbar = wick / candle_range > 0.50
            directional_candle = (df["close"] > df["open"] if sign > 0
                                  else df["close"] < df["open"])
            c_trigger = np.select([body_engulf | pinbar, directional_candle],
                                  [20.0, 10.0], default=0.0)
            volume_delta = (df["volume"]
                            / df[f"pullback_avg_volume_{suffix}"].replace(
                                0.0, np.nan))
            c_volume = np.select([volume_delta >= 2.0,
                                  volume_delta >= 1.2],
                                 [15.0, 8.0], default=0.0)
            c_trend = np.where(trend_aligned, 10.0, 0.0)
            confidence = (c_level + c_vwap + c_trigger + c_volume + c_trend
                          + regime / 70.0 * 15.0)
            df[f"confidence_{suffix}"] = confidence
            df[f"target_r_{suffix}"] = np.where(
                confidence < 68.0, p.grade_c_tp1_r, p.tp1_r)

    def _conditions(self, df: pd.DataFrame, direction: Direction) -> pd.Series:
        p = self.settings
        suffix = direction.value
        sign = 1.0 if direction == Direction.LONG else -1.0
        rvol_floor = p.min_rvol_trigger + (p.short_rvol_add if sign < 0 else 0)
        vwap_aligned = (df["close"] > df["vwap"] if sign > 0
                        else df["close"] < df["vwap"])
        window = ((df["bar_close_minute"] >= p.entry_start_minute)
                  & (df["bar_close_minute"] <= p.entry_end_minute))
        raw = (df[f"trigger_base_{suffix}"] & vwap_aligned
               & (df["rvol"] >= rvol_floor)
               & (df["adt20"] >= 500_000_000) & (df["natr20"] >= 1.0)
               & (df[f"regime_{suffix}"] >= p.min_regime_score)
               & (df[f"confidence_{suffix}"] >= p.min_confidence_score)
               & window)
        day = local_dates(df).dt.normalize()
        return (raw & (raw.groupby(day).cumsum() == 1)).fillna(False)

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
        return (max(float(previous["high"]), float(row["open"]))
                if direction == Direction.LONG else
                min(float(previous["low"]), float(row["open"])))

    def signal_diagnostics(self, dataframe: pd.DataFrame,
                           symbol: str) -> list:
        if dataframe.empty or self.missing_columns(dataframe):
            return []
        p, row = self.settings, dataframe.iloc[-1]
        out = []
        for direction in (Direction.LONG, Direction.SHORT):
            suffix = direction.value
            sign = 1.0 if direction == Direction.LONG else -1.0
            state_failure = bool(row[f"depth_failure_{suffix}"]
                                 or row[f"timeout_failure_{suffix}"])
            attempted = bool(row[f"trigger_attempt_{suffix}"])
            accepted = bool(self._conditions(dataframe, direction).iloc[-1])
            if accepted or not (attempted or state_failure):
                continue
            reasons = []
            if row[f"depth_failure_{suffix}"]:
                reasons.append("retest_depth_exceeded")
            if row[f"timeout_failure_{suffix}"]:
                reasons.append("retest_bars_exceeded")
            if attempted and not row[f"trigger_base_{suffix}"]:
                reasons.append("wave_extension_too_small")
            rvol_floor = p.min_rvol_trigger + (p.short_rvol_add if sign < 0 else 0)
            checks = [
                (float(row["rvol"]) >= rvol_floor, "rvol"),
                ((row["close"] > row["vwap"]) if sign > 0
                 else (row["close"] < row["vwap"]), "vwap"),
                (float(row[f"regime_{suffix}"]) >= p.min_regime_score,
                 "regime"),
                (float(row[f"confidence_{suffix}"]) >= p.min_confidence_score,
                 "confidence"),
            ]
            reasons.extend(name for passed, name in checks if not passed)
            out.append({
                "symbol": symbol, "strategy": self.name,
                "direction": suffix, "accepted": False,
                "reasons": reasons,
                "regime": float(row.get(f"regime_{suffix}", 0.0)),
                "confidence": float(row.get(f"confidence_{suffix}", 0.0)),
                "bar_time": str(pd.Timestamp(row["date"])),
            })
        return out

    def regime_score(self, dataframe: pd.DataFrame,
                     direction: Direction) -> float:
        if dataframe.empty:
            return 0.0
        return clip01(float(dataframe[f"regime_{direction.value}"].iloc[-1])
                      / 70.0)

    def confidence_for(self, dataframe: pd.DataFrame,
                       direction: Direction) -> ConfidenceScore:
        if dataframe.empty:
            return ConfidenceScore.zero("empty frame")
        suffix = direction.value
        points = float(dataframe[f"confidence_{suffix}"].iloc[-1])
        regime = float(dataframe[f"regime_{suffix}"].iloc[-1])
        return ConfidenceScore(
            score=clip01(points / 100.0),
            components={
                "specification_score": {
                    "score": round(points / 100.0, 4), "weight": 1.0,
                    "detail": f"{points:.1f}/100"},
                "regime": {"score": round(regime / 70.0, 4), "weight": 0.0,
                           "detail": f"{regime:.1f}/70"},
            }, reason=f"STRAT-02 {suffix} confidence {points:.1f}/100")

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if dataframe.empty or "confidence_long" not in dataframe:
            return ConfidenceScore.zero("context not prepared")
        direction = (Direction.LONG
                     if dataframe["confidence_long"].iloc[-1]
                     >= dataframe["confidence_short"].iloc[-1]
                     else Direction.SHORT)
        return self.confidence_for(dataframe, direction)
