"""STRAT-01 - specification-compliant 5-minute Opening Range Breakout.

This is the sole canonical ORB implementation. The retired ``orb_15m`` name is
kept only in historical evidence and documentation; it is not registered.

Two optional source inputs cannot be reproduced consistently from the stored
market data: historical bid/ask spread and point-in-time NIFTY50 constituent
membership. Liquidity therefore receives the conservative five-point ADT score
when the turnover floor is met (never the spread-dependent ten), and the live
top-500 scan universe is the documented breadth proxy. No missing component is
fabricated or rescaled.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import (
    adx, atr, crossed_above, crossed_below, ema, macd_histogram,
    opening_range, roc, session_vwap, slot_relative_volume,
)
from algo.execution import ExecutionSpec
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import ConfidenceScore, clip01
from algo.strategies.opening_context import (
    add_opening_market_context, local_dates, shared_5m_features,
)


@dataclass(frozen=True)
class OrbParams:
    range_minutes: int = 5
    breakout_buffer_pct: float = 0.001
    min_rvol: float = 2.0
    short_rvol_add: float = 0.5
    rvol_sessions: int = 10
    atr_period: int = 14
    max_or_width_atr: float = 2.5
    min_or_width_atr: float = 0.5
    min_regime_score: float = 42.0
    min_confidence_score: float = 55.0
    risk_per_trade_pct: float = 0.01
    max_capital_per_trade: float = 0.20
    max_stop_atr: float = 1.5
    tp1_r: float = 1.5
    tp1_fraction: float = 0.50
    chandelier_atr: float = 2.0
    slippage_collar_pct: float = 0.001
    no_progress_bars: int = 6
    no_progress_r: float = 0.5
    entry_start_minute: int = 9 * 60 + 20
    entry_end_minute: int = 14 * 60 + 45

    @classmethod
    def from_dict(cls, data) -> "OrbParams":
        return from_dict(cls, data)


class OpeningRangeBreakout(StrategyProfile):

    meta = StrategyMeta(
        name="orb_5m", version="2.0.0", spec_id="STRAT-01",
        exclusive_group="opening_breakout", direction=Direction.BOTH,
        active_conflict_group="opening_or_retest",
        blocked_by_pre_partial_groups=("dve_breakout",),
        blocked_by_session_groups=("opening_drive", "gap_go"),
        blocked_by_timed_groups=("liquidity_sweep",),
        holding_scope=HoldingScope.INTRADAY, timeframe="5m", min_bars=1502,
        required_columns=(
            "close", "or_high", "or_low", "or_mid", "after_range", "atr",
            "vwap", "ema9", "ema20", "rvol", "regime_long", "regime_short",
            "confidence_long", "confidence_short", "natr20"),
        supported_regimes=("trend",),
        hypothesis=("A buffered, volume-confirmed break of the first 5-minute "
                    "auction range persists when stock and index structure "
                    "show aligned institutional participation."),
        expected_behaviour=("At most one opening-breakout allocation per "
                            "symbol/session, long or short, between 09:20 and "
                            "14:45 IST."),
        known_failure_modes=(
            "false breaks during index/stock divergence",
            "news gaps whose opening range is already exhausted",
            "thin names where candle volume overstates executable liquidity",
        ), enabled=True)

    execution = ExecutionSpec(
        entry="limit_collar", slippage_collar_pct=0.001,
        stop_kind="column_atr_cap", stop_col="or_mid", stop_atr_mult=1.5,
        hard_stop_pct=None, target_kind="r", target_r=1.5,
        partial_fraction=0.5, trail="chandelier", trail_atr_mult=2.0,
        trail_after_partial=True,
        invalidation_long_col="invalidate_long",
        invalidation_short_col="invalidate_short",
        no_progress_bars=6, no_progress_r=0.5,
        risk_per_trade_pct=0.01, max_capital_per_trade=0.20,
        intraday=True, allow_overnight=False, max_hold_bars=None)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or OrbParams())

    @property
    def context_symbols(self) -> tuple:
        return ("NIFTY50",)

    def min_history(self) -> int:
        # 20 completed sessions for ADT/gap statistics plus current session.
        return int(self.meta.min_bars)

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = shared_5m_features(dataframe, p.atr_period, p.rvol_sessions)
        or_high, or_low, after = opening_range(df, p.range_minutes)
        df["or_high"], df["or_low"], df["after_range"] = or_high, or_low, after
        df["or_mid"] = (or_high + or_low) / 2.0
        df["or_width"] = or_high - or_low
        df["atr_mean20"] = df["atr"].rolling(20, min_periods=20).mean()
        df["adx"] = adx(df, 14)
        df["roc20"] = roc(df["close"], 20)
        df["macd_hist"] = macd_histogram(df["close"])
        df["ema50"] = ema(df["close"], 50)
        local = local_dates(df)
        day = local.dt.normalize()
        first_rvol = df["rvol"].where(~after).groupby(day).transform("first")
        df["opening_rvol"] = first_rvol
        df["invalidate_long"] = df["close"] < df["vwap"]
        df["invalidate_short"] = df["close"] > df["vwap"]
        return df

    def prepare_context(self, frames: dict, context: dict) -> dict:
        if not frames:
            return frames
        add_opening_market_context(frames, context)
        for df in frames.values():
            self._add_scores(df)
        return frames

    def _add_scores(self, df: pd.DataFrame) -> None:
        for direction in (Direction.LONG, Direction.SHORT):
            suffix = direction.value
            sign = 1.0 if direction == Direction.LONG else -1.0
            aligned = df["nifty_trend"] == sign
            s1 = np.select([(df["adx"] >= 25) & aligned,
                            (df["adx"] >= 18) & (df["adx"] < 25)],
                           [10.0, 5.0], default=0.0)
            momentum10 = ((sign * df["roc20"] >= 0.015)
                          & (sign * df["macd_hist"] > 0))
            momentum5 = ((sign * df["roc20"] >= 0.005)
                         & (sign * df["roc20"] < 0.015))
            s2 = np.select([momentum10, momentum5], [10.0, 5.0], default=0.0)
            expansion = df["atr"] / df["atr_mean20"].replace(0.0, np.nan)
            s3 = np.select([expansion >= 1.25, expansion >= 1.0],
                           [10.0, 5.0], default=0.0)
            # Spread history is unavailable: never award the 10-point branch.
            s4 = np.where(df["adt20"] >= 500_000_000, 5.0, 0.0)
            ratio = (df["ad_ratio"] if direction == Direction.LONG else
                     (1.0 / df["ad_ratio"].replace(0.0, np.nan)).where(
                         df["ad_ratio"] != 0.0, np.inf))
            breadth = (df["breadth_above_vwap"] if direction == Direction.LONG
                       else 1.0 - df["breadth_above_vwap"])
            s5 = np.select([(ratio >= 2.0) & (breadth > 0.65), ratio >= 1.2],
                           [10.0, 5.0], default=0.0)
            s6 = np.select([df["gap_ratio"] >= 1.5,
                            df["gap_ratio"] >= 0.8],
                           [10.0, 5.0], default=0.0)
            s7 = np.select([df["opening_rvol"] >= 2.5,
                            df["opening_rvol"] >= 1.5],
                           [10.0, 5.0], default=0.0)
            regime = s1 + s2 + s3 + s4 + s5 + s6 + s7
            df[f"regime_{suffix}"] = regime

            vwap_delta = sign * (df["close"] / df["vwap"] - 1.0)
            c1 = np.select([vwap_delta > 0.002, vwap_delta > 0],
                           [20.0, 10.0], default=0.0)
            strict_ema = ((df["ema9"] > df["ema20"])
                          & (df["ema20"] > df["ema50"]) if sign > 0 else
                          (df["ema9"] < df["ema20"])
                          & (df["ema20"] < df["ema50"]))
            basic_ema = (df["ema9"] > df["ema20"] if sign > 0
                         else df["ema9"] < df["ema20"])
            c2 = np.select([strict_ema, basic_ema], [15.0, 8.0], default=0.0)
            c3 = np.select([df["rvol"] >= 3.0, df["rvol"] >= 2.0],
                           [25.0, 15.0], default=0.0)
            c4 = np.where(aligned, 10.0, 0.0)
            gap = df["open"] / df["prior_close"] - 1.0
            c5 = np.select([sign * gap > 0,
                            gap.abs() <= self.settings.breakout_buffer_pct],
                           [10.0, 5.0], default=0.0)
            confidence = c1 + c2 + c3 + c4 + c5 + regime / 70.0 * 20.0
            df[f"confidence_{suffix}"] = confidence

    def _conditions(self, df: pd.DataFrame, direction: Direction) -> pd.Series:
        p = self.settings
        sign = 1.0 if direction == Direction.LONG else -1.0
        suffix = direction.value
        threshold = (df["or_high"] * (1 + p.breakout_buffer_pct)
                     if sign > 0 else
                     df["or_low"] * (1 - p.breakout_buffer_pct))
        breakout = (crossed_above(df["close"], threshold) if sign > 0
                    else crossed_below(df["close"], threshold))
        rvol_min = p.min_rvol + (p.short_rvol_add if sign < 0 else 0.0)
        structure = ((df["close"] > df["vwap"])
                     & (df["ema9"] > df["ema20"]) if sign > 0 else
                     (df["close"] < df["vwap"])
                     & (df["ema9"] < df["ema20"]))
        # Approved interpretation: the explicit long/short NIFTY VWAP table is
        # a hard alignment gate in addition to the L1-L9 list.
        nifty_alignment = (df["nifty_close"] > df["nifty_vwap"] if sign > 0
                           else df["nifty_close"] < df["nifty_vwap"])
        width = df["or_width"] / df["atr"].replace(0.0, np.nan)
        window = ((df["bar_close_minute"] >= p.entry_start_minute)
                  & (df["bar_close_minute"] <= p.entry_end_minute))
        return (df["after_range"] & breakout & (df["rvol"] >= rvol_min)
                & structure & nifty_alignment
                & (df["natr20"] >= 1.0)
                & (width >= p.min_or_width_atr)
                & (width <= p.max_or_width_atr)
                & (df[f"regime_{suffix}"] >= p.min_regime_score)
                & (df[f"confidence_{suffix}"] >= p.min_confidence_score)
                & window).fillna(False)

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
        p = self.settings
        row = dataframe.iloc[index]
        return (float(row["or_high"]) * (1 + p.breakout_buffer_pct)
                if direction == Direction.LONG else
                float(row["or_low"]) * (1 - p.breakout_buffer_pct))

    def signal_diagnostics(self, dataframe: pd.DataFrame,
                           symbol: str) -> list:
        """Log only genuine boundary attempts, not every ordinary scan row."""
        if dataframe.empty or self.missing_columns(dataframe):
            return []
        p = self.settings
        row = dataframe.iloc[-1]
        out = []
        for direction in (Direction.LONG, Direction.SHORT):
            sign = 1.0 if direction == Direction.LONG else -1.0
            suffix = direction.value
            trigger = (float(row["or_high"]) * (1 + p.breakout_buffer_pct)
                       if sign > 0 else
                       float(row["or_low"]) * (1 - p.breakout_buffer_pct))
            attempted = (float(row["close"]) > trigger if sign > 0
                         else float(row["close"]) < trigger)
            if not attempted or bool(self._conditions(
                    dataframe, direction).iloc[-1]):
                continue
            reasons = []
            rvol_floor = p.min_rvol + (p.short_rvol_add if sign < 0 else 0)
            width = float(row["or_width"] / row["atr"]) if row["atr"] else np.nan
            checks = [
                (float(row["rvol"]) >= rvol_floor, "rvol"),
                ((float(row["close"]) > float(row["vwap"])) if sign > 0
                 else (float(row["close"]) < float(row["vwap"])), "vwap"),
                ((float(row["ema9"]) > float(row["ema20"])) if sign > 0
                 else (float(row["ema9"]) < float(row["ema20"])), "ema"),
                (float(row["natr20"]) >= 1.0, "natr20"),
                (p.min_or_width_atr <= width <= p.max_or_width_atr,
                 "or_width"),
                (float(row[f"regime_{suffix}"]) >= p.min_regime_score,
                 "regime"),
                (float(row[f"confidence_{suffix}"]) >= p.min_confidence_score,
                 "confidence"),
                (p.entry_start_minute <= float(row["bar_close_minute"])
                 <= p.entry_end_minute, "entry_window"),
            ]
            reasons.extend(name for passed, name in checks if not passed)
            out.append({
                "symbol": symbol, "strategy": self.name,
                "direction": suffix, "accepted": False,
                "reasons": reasons, "regime": float(row[f"regime_{suffix}"]),
                "confidence": float(row[f"confidence_{suffix}"]),
                "rvol": float(row["rvol"]), "trigger": trigger,
                "bar_time": str(pd.Timestamp(row["date"])),
            })
        return out

    def regime_score(self, dataframe: pd.DataFrame,
                     direction: Direction) -> float:
        col = f"regime_{direction.value}"
        if dataframe.empty or col not in dataframe:
            return 0.0
        return clip01(float(dataframe[col].iloc[-1]) / 70.0)

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
                "specification_score": {"score": round(points / 100.0, 4),
                                        "weight": 1.0,
                                        "detail": f"{points:.1f}/100"},
                "regime": {"score": round(regime / 70.0, 4),
                           "weight": 0.0, "detail": f"{regime:.1f}/70"},
            },
            reason=f"STRAT-01 {direction.value} confidence {points:.1f}/100")

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if dataframe.empty or "confidence_long" not in dataframe:
            return ConfidenceScore.zero("context not prepared")
        direction = (Direction.LONG
                     if dataframe["confidence_long"].iloc[-1]
                     >= dataframe["confidence_short"].iloc[-1]
                     else Direction.SHORT)
        return self.confidence_for(dataframe, direction)
