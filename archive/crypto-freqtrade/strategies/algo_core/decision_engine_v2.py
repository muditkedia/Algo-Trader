"""algo_core.decision_engine_v2 - v2 GO / NO-GO engine (Phases D/E).

Preserves the mandatory GO architecture and reuses v1's mandatory checks
(trend, market structure, liquidity, hard-stop cap - all proven-valuable or
structural). REMOVES the components proven to carry no predictive signal:
  * advisory RSI / volume / volatility scoring (winner score == loser score)
  * the risk_reward gate (near-constant ~1.5, uncorrelated with outcome)
ADDS two evidence-based mandatory gates:
  * anti_chase - reject entries extended above the 5m fast EMA or following a
                 sharp 1h run-up (Phase E: chasing is harmful)
  * cost_gate  - expected reward (observed MFE ~2x ATR%) must exceed 2x the
                 round-trip cost (Phase E: edge is smaller than fees)

There is no conviction score in v2: GO requires every mandatory gate to pass.
"""

from __future__ import annotations

from typing import Optional

from algo_core.decision_engine import (
    CheckResult, Decision, DecisionEngine, RejectionRecorder, TradeContext, _value,
)
from algo_core.settings import EngineParams, RiskParams
from algo_core.settings_v2 import V2Params


class DecisionEngineV2(DecisionEngine):
    """v2 engine: mandatory-only gates, no advisory score, + anti-chase + cost."""

    def __init__(self, params: EngineParams, risk_params: RiskParams,
                 v2: V2Params, recorder: Optional[RejectionRecorder] = None) -> None:
        super().__init__(params, risk_params, recorder)
        self.v2 = v2

    def evaluate(self, ctx: TradeContext, source: str = "") -> Decision:
        checks = [
            # --- preserved mandatory gates (reused from v1) ---
            self._check_htf_trend(ctx),
            self._check_itf_trend(ctx),
            self._check_entry_trend(ctx),        # retains the ADX-15m floor
            self._check_market_structure(ctx),
            self._check_liquidity(ctx),
            self._check_spread(ctx),
            self._check_position_sizing(ctx),    # hard-stop cap preserved
            # --- new evidence-based mandatory gates ---
            self._check_anti_chase(ctx),
            self._check_cost(ctx),
        ]
        reasons = [f"{c.name}: {c.reason}" for c in checks if not c.passed]
        decision = Decision(
            go=not reasons,
            score=1.0 if not reasons else 0.0,   # v2 has no advisory score
            checks=checks,
            rejection_reasons=reasons,
        )
        if not decision.go and self.recorder:
            self.recorder.record(ctx, decision, source)
        return decision

    def _check_anti_chase(self, ctx: TradeContext) -> CheckResult:
        ext = _value(ctx.row, "dist_fast_5m")
        mom = _value(ctx.row, "mom_1h")
        if ext is None or mom is None:
            return CheckResult("anti_chase", "entry", False, False, 0.0,
                               "missing data: dist_fast_5m / mom_1h")
        passed = (ext <= self.v2.max_ext_above_fast_5m
                  and mom <= self.v2.max_mom_1h)
        return CheckResult(
            "anti_chase", "entry", False, passed, 1.0 if passed else 0.0,
            f"ext_5m={ext:.4f} (max {self.v2.max_ext_above_fast_5m}), "
            f"mom_1h={mom:.4f} (max {self.v2.max_mom_1h})")

    def _check_cost(self, ctx: TradeContext) -> CheckResult:
        atr_pct = _value(ctx.row, "atr_pct_15m")
        if atr_pct is None:
            return CheckResult("cost_gate", "risk", False, False, 0.0,
                               "missing data: atr_pct_15m")
        expected_reward = self.v2.empirical_mfe_per_atr * atr_pct
        required = self.v2.min_reward_cost_multiple * self.v2.round_trip_cost
        passed = expected_reward >= required
        return CheckResult(
            "cost_gate", "risk", False, passed, 1.0 if passed else 0.0,
            f"expected_reward={expected_reward:.4f} "
            f"(~{self.v2.empirical_mfe_per_atr}x ATR%={atr_pct:.4f}) "
            f">= required {required:.4f}")
