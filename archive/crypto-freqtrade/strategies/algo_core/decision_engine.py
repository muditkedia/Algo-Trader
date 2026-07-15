"""GO / NO-GO decision engine.

Two distinct kinds of check:

* **Mandatory** (hard gates) - every one must pass or the trade is NO-GO.
  Categories: trend, market_structure, liquidity, risk. These do NOT
  contribute to the conviction score.
* **Advisory** (soft signals) - RSI, volume, volatility. They never block on
  their own; instead they feed the weighted conviction score. A trade is GO
  only if all mandatory checks pass AND the advisory score reaches
  ``min_score``.

This separation makes the score meaningful (it measures advisory quality
only) and keeps the mandatory gates unambiguous.

Every NO-GO is recorded (structured JSONL + log line) with the exact reasons
and the detected market regime, so rejected trades are fully auditable.

The engine is stateless: it evaluates a single analyzed candle row (5m base
columns plus ``_15m`` / ``_1h`` / ``_4h`` suffixes) plus live context (stake,
spread, regime). All thresholds come from ``algo_core.settings``.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from algo_core.risk_engine import initial_stop_pct, stop_is_tradeable
from algo_core.settings import EngineParams, RiskParams

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    name: str
    category: str
    advisory: bool
    passed: bool
    score: float
    reason: str = ""


@dataclass
class Decision:
    go: bool
    score: float
    checks: list = field(default_factory=list)
    rejection_reasons: list = field(default_factory=list)


@dataclass
class TradeContext:
    """Inputs for one evaluation: the analyzed candle row plus live context."""

    pair: str
    side: str
    row: pd.Series
    stake_amount: float
    spread_pct: Optional[float] = None  # None when the order book is unavailable
    regime: str = "unknown"


def _value(row: pd.Series, key: str) -> Optional[float]:
    """Numeric value from the row, or None when missing / NaN."""
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


class DecisionEngine:
    """Scores a candidate entry, gates on mandatory checks, records rejections."""

    def __init__(
        self,
        params: EngineParams,
        risk_params: RiskParams,
        recorder: Optional["RejectionRecorder"] = None,
    ) -> None:
        self.params = params
        self.risk_params = risk_params
        self.recorder = recorder

    # ------------------------------------------------------------------ API

    def evaluate(self, ctx: TradeContext, source: str = "") -> Decision:
        checks = [
            # --- mandatory gates ---
            self._check_htf_trend(ctx),
            self._check_itf_trend(ctx),
            self._check_entry_trend(ctx),
            self._check_market_structure(ctx),
            self._check_liquidity(ctx),
            self._check_spread(ctx),
            self._check_risk_reward(ctx),
            self._check_position_sizing(ctx),
            # --- advisory (score only) ---
            self._check_momentum(ctx),
            self._check_volume(ctx),
            self._check_volatility(ctx),
        ]
        score = self._conviction_score(checks)

        rejection_reasons = [
            f"{c.name}: {c.reason}"
            for c in checks
            if not c.advisory and not c.passed
        ]
        if not rejection_reasons and score < self.params.min_score:
            rejection_reasons.append(
                f"conviction: advisory score {score:.2f} below "
                f"minimum {self.params.min_score:.2f}"
            )

        decision = Decision(
            go=not rejection_reasons,
            score=round(score, 4),
            checks=checks,
            rejection_reasons=rejection_reasons,
        )
        if not decision.go and self.recorder:
            self.recorder.record(ctx, decision, source)
        return decision

    def _conviction_score(self, checks: list) -> float:
        """Weighted average of advisory check scores (0..1)."""
        weights = {
            "momentum": self.params.weight_rsi,
            "volume": self.params.weight_volume,
            "volatility": self.params.weight_volatility,
        }
        advisory = [c for c in checks if c.advisory]
        total = sum(weights.get(c.name, 0.0) for c in advisory)
        if total <= 0:
            return 0.0
        return sum(weights.get(c.name, 0.0) * c.score for c in advisory) / total

    # ------------------------------------------------------- mandatory checks

    def _fail_missing(self, name: str, category: str, keys: list) -> CheckResult:
        return CheckResult(
            name, category, False, False, 0.0, f"missing data: {', '.join(keys)}"
        )

    def _check_htf_trend(self, ctx: TradeContext) -> CheckResult:
        """4h regime: fast EMA above slow EMA and price above the slow EMA."""
        ef, es, close = (
            _value(ctx.row, "ema_fast_4h"),
            _value(ctx.row, "ema_slow_4h"),
            _value(ctx.row, "close"),
        )
        if None in (ef, es, close):
            return self._fail_missing("htf_trend", "trend", ["ema_fast_4h", "ema_slow_4h"])
        passed = ef > es and close > es
        return CheckResult(
            "htf_trend", "trend", False, passed, 1.0 if passed else 0.0,
            f"4h ema_fast {'>' if ef > es else '<='} ema_slow, "
            f"close {'>' if close > es else '<='} ema_slow",
        )

    def _check_itf_trend(self, ctx: TradeContext) -> CheckResult:
        """1h trend: EMA alignment, price above slow EMA, ADX confirms strength."""
        ef, es, adx, close = (
            _value(ctx.row, "ema_fast_1h"),
            _value(ctx.row, "ema_slow_1h"),
            _value(ctx.row, "adx_1h"),
            _value(ctx.row, "close"),
        )
        if None in (ef, es, adx, close):
            return self._fail_missing("itf_trend", "trend", ["ema_fast_1h", "adx_1h"])
        passed = ef > es and close > es and adx >= self.params.adx_min_1h
        return CheckResult(
            "itf_trend", "trend", False, passed, 1.0 if passed else 0.0,
            f"adx_1h={adx:.1f} (min {self.params.adx_min_1h}), "
            f"aligned={ef > es and close > es}",
        )

    def _check_entry_trend(self, ctx: TradeContext) -> CheckResult:
        """15m + 5m execution alignment."""
        keys = ["ema_fast", "ema_slow", "ema_fast_15m", "ema_slow_15m", "adx_15m", "close"]
        vals = {k: _value(ctx.row, k) for k in keys}
        if None in vals.values():
            return self._fail_missing("entry_trend", "trend", keys)
        passed = (
            vals["ema_fast_15m"] > vals["ema_slow_15m"]
            and vals["adx_15m"] >= self.params.adx_min_15m
            and vals["ema_fast"] > vals["ema_slow"]
            and vals["close"] > vals["ema_fast"]
        )
        return CheckResult(
            "entry_trend", "trend", False, passed, 1.0 if passed else 0.0,
            f"adx_15m={vals['adx_15m']:.1f} (min {self.params.adx_min_15m}), "
            f"15m/5m EMA alignment={passed}",
        )

    def _check_market_structure(self, ctx: TradeContext) -> CheckResult:
        """Rising 15m swing lows and 1h closes above their recent history."""
        structure = _value(ctx.row, "structure_up_15m")
        higher = _value(ctx.row, "closing_higher_1h")
        if structure is None or higher is None:
            return self._fail_missing(
                "market_structure", "market_structure",
                ["structure_up_15m", "closing_higher_1h"],
            )
        passed = structure > 0.5 and higher > 0.5
        return CheckResult(
            "market_structure", "market_structure", False, passed,
            1.0 if passed else 0.0,
            f"15m swing lows rising={structure > 0.5}, 1h closing higher={higher > 0.5}",
        )

    def _check_liquidity(self, ctx: TradeContext) -> CheckResult:
        """Average traded quote volume must clear the liquidity floor."""
        vol_mean, close = _value(ctx.row, "volume_mean"), _value(ctx.row, "close")
        if None in (vol_mean, close):
            return self._fail_missing("liquidity", "liquidity", ["volume_mean"])
        quote_volume = vol_mean * close
        passed = quote_volume >= self.params.min_quote_volume
        return CheckResult(
            "liquidity", "liquidity", False, passed, 1.0 if passed else 0.0,
            f"avg quote volume={quote_volume:,.0f} "
            f"(min {self.params.min_quote_volume:,.0f})",
        )

    def _check_spread(self, ctx: TradeContext) -> CheckResult:
        """Order-book spread gate - mandatory when available, skipped otherwise.

        The order book only exists in dry-run/live; in backtesting it is
        unavailable and this gate passes by design.
        """
        if ctx.spread_pct is None:
            return CheckResult(
                "spread", "liquidity", False, True, 1.0,
                "order book unavailable - skipped",
            )
        passed = ctx.spread_pct <= self.params.max_spread_pct
        return CheckResult(
            "spread", "liquidity", False, passed, 1.0 if passed else 0.0,
            f"spread={ctx.spread_pct:.5f} (max {self.params.max_spread_pct})",
        )

    def _stop_pct(self, ctx: TradeContext) -> Optional[float]:
        return initial_stop_pct(
            _value(ctx.row, "close"),
            _value(ctx.row, "atr_15m"),
            _value(ctx.row, "swing_low_15m"),
            self.risk_params,
        )

    def _check_risk_reward(self, ctx: TradeContext) -> CheckResult:
        """Expected reward (ATR multiple) vs. the structure-aware stop distance."""
        close, atr = _value(ctx.row, "close"), _value(ctx.row, "atr_15m")
        stop_pct = self._stop_pct(ctx)
        if None in (close, atr) or not stop_pct:
            return self._fail_missing("risk_reward", "risk", ["atr_15m", "swing_low_15m"])
        reward_pct = self.risk_params.reward_atr_multiple * atr / close
        rr = reward_pct / stop_pct
        passed = rr >= self.risk_params.min_risk_reward
        return CheckResult(
            "risk_reward", "risk", False, passed, 1.0 if passed else 0.0,
            f"rr={rr:.2f} (min {self.risk_params.min_risk_reward}), "
            f"stop={stop_pct:.4f}, reward={reward_pct:.4f}",
        )

    def _check_position_sizing(self, ctx: TradeContext) -> CheckResult:
        """Stake must be positive and the required stop must fit the hard stop."""
        stop_pct = self._stop_pct(ctx)
        if stop_pct is None:
            return self._fail_missing("position_sizing", "risk", ["atr_15m"])
        stop_ok = stop_is_tradeable(stop_pct, self.risk_params)
        passed = stop_ok and ctx.stake_amount > 0
        reason = (
            f"stake={ctx.stake_amount:.2f}, required stop={stop_pct:.4f} "
            f"(hard cap {self.risk_params.hard_stop_pct})"
        )
        if not stop_ok:
            reason = "required stop exceeds hard emergency stop - " + reason
        return CheckResult(
            "position_sizing", "risk", False, passed, 1.0 if passed else 0.0, reason
        )

    # -------------------------------------------------------- advisory checks

    def _check_momentum(self, ctx: TradeContext) -> CheckResult:
        """RSI in the healthy-momentum band - trending but not overbought."""
        rsi = _value(ctx.row, "rsi_15m")
        if rsi is None:
            return CheckResult("momentum", "momentum", True, False, 0.0,
                               "missing data: rsi_15m")
        passed = self.params.rsi_min <= rsi <= self.params.rsi_max
        score = max(0.4, 1 - abs(rsi - self.params.rsi_ideal) / 25) if passed else 0.0
        return CheckResult(
            "momentum", "momentum", True, passed, score,
            f"rsi_15m={rsi:.1f} (band {self.params.rsi_min}-{self.params.rsi_max})",
        )

    def _check_volume(self, ctx: TradeContext) -> CheckResult:
        """Current 5m volume relative to its recent mean."""
        ratio = _value(ctx.row, "volume_ratio")
        if ratio is None:
            return CheckResult("volume", "volume", True, False, 0.0,
                               "missing data: volume_ratio")
        passed = ratio >= self.params.volume_ratio_min
        score = min(1.0, ratio / 2) if passed else 0.0
        return CheckResult(
            "volume", "volume", True, passed, score,
            f"volume_ratio={ratio:.2f} (min {self.params.volume_ratio_min})",
        )

    def _check_volatility(self, ctx: TradeContext) -> CheckResult:
        """15m ATR%% inside the tradeable band - not dead, not chaotic."""
        atr_pct = _value(ctx.row, "atr_pct_15m")
        if atr_pct is None:
            return CheckResult("volatility", "volatility", True, False, 0.0,
                               "missing data: atr_pct_15m")
        passed = self.params.atr_pct_min <= atr_pct <= self.params.atr_pct_max
        return CheckResult(
            "volatility", "volatility", True, passed, 1.0 if passed else 0.0,
            f"atr_pct_15m={atr_pct:.4f} "
            f"(band {self.params.atr_pct_min}-{self.params.atr_pct_max})",
        )


class RejectionRecorder:
    """Appends every NO-GO decision to a JSONL audit file (and the log)."""

    def __init__(self, path: Optional[Path]) -> None:
        self.path = Path(path) if path else None

    def record(self, ctx: TradeContext, decision: Decision, source: str) -> None:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pair": ctx.pair,
            "side": ctx.side,
            "regime": ctx.regime,
            "source": source,
            "score": decision.score,
            "rejection_reasons": decision.rejection_reasons,
            "checks": {
                c.name: {
                    "category": c.category,
                    "advisory": c.advisory,
                    "passed": c.passed,
                    "score": round(c.score, 3),
                    "reason": c.reason,
                }
                for c in decision.checks
            },
        }
        logger.info(
            "NO-GO %s (score %.2f): %s",
            ctx.pair, decision.score, "; ".join(decision.rejection_reasons),
        )
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload) + "\n")
        except OSError as exc:  # never let audit logging break trading
            logger.warning("Could not write rejection record: %s", exc)
