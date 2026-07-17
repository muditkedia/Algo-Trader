"""Opportunity Ranking Engine - configurable, evidence-based, persisted.

Replaces plain confidence ordering with a weighted combination of normalized
components:

    expected_return · hist_expectancy · hist_win_rate · confidence ·
    calibration · liquidity · reliability · cost (penalty) · risk_reward

NOTHING is hardcoded: weights and normalization scales are frozen dataclasses
built ``from_dict`` (config). Historical inputs come from the evidence database
(latest overall evaluation per strategy + the confidence-calibration
correlation); a strategy with no evidence scores those components at the
neutral midpoint rather than being fabricated up or punished to zero.

Every ranking decision is persisted: the scanner writes each signal with its
rank and the full per-component ranking breakdown (under the ``_ranking`` key
of the signal's component JSON), so any past ranking can be audited.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from algo.core.config import from_dict
from algo.evidence import queries
from algo.strategies.confidence import clip01

NEUTRAL = 0.5   # score for components with no evidence yet


@dataclass(frozen=True)
class RankingWeights:
    """Relative weights (any non-negative scale; normalized internally)."""

    expected_return: float = 0.20
    hist_expectancy: float = 0.15
    hist_win_rate: float = 0.10
    confidence: float = 0.20
    calibration: float = 0.05
    liquidity: float = 0.10
    reliability: float = 0.10
    cost: float = 0.05
    risk_reward: float = 0.05

    @classmethod
    def from_dict(cls, data) -> "RankingWeights":
        return from_dict(cls, data)


@dataclass(frozen=True)
class RankingScales:
    """Normalization anchors (value that maps to a full score of 1.0)."""

    expectancy_full: float = 0.01       # +1% net expectancy per trade
    win_rate_full: float = 0.65         # 65% win rate
    risk_reward_full: float = 3.0
    liquidity_volume_ratio_full: float = 2.0
    cost_full: float = 0.005            # 50 bps round trip scores 0
    reliability_trades_full: int = 200  # sample size for full reliability

    @classmethod
    def from_dict(cls, data) -> "RankingScales":
        return from_dict(cls, data)


class OpportunityRanker:
    def __init__(self, weights: Optional[RankingWeights] = None,
                 scales: Optional[RankingScales] = None,
                 db=None) -> None:
        self.weights = weights or RankingWeights()
        self.scales = scales or RankingScales()
        self.db = db
        self._stats: Dict[str, dict] = {}
        self._calibration: Dict[str, Optional[float]] = {}

    # ----------------------------------------------------------- evidence in

    def refresh_stats(self) -> None:
        """Reload per-strategy evidence (call once per scan cycle)."""
        if self.db is None:
            return
        self._stats = queries.strategy_overall_stats(self.db)
        self._calibration = {}
        for name in self._stats:
            strategy_id = queries.strategy_id_by_name(self.db, name)
            self._calibration[name] = (
                queries.confidence_outcome_correlation(self.db, strategy_id)
                if strategy_id is not None else None)

    def stats_for(self, strategy: str) -> dict:
        return self._stats.get(strategy, {})

    # -------------------------------------------------------------- scoring

    def score(self, opp) -> tuple:
        """(weighted score 0..1, per-component breakdown) for one opportunity."""
        w, s = self.weights, self.scales
        stats = self.stats_for(opp.strategy)

        def norm(value, full, neutral_when_none=True):
            if value is None:
                return NEUTRAL if neutral_when_none else 0.0
            return clip01(value / full) if full else 0.0

        expectancy = stats.get("expectancy")
        win_rate = stats.get("win_rate")
        n_trades = stats.get("n_trades") or 0
        calibration = self._calibration.get(opp.strategy)

        components = {
            "expected_return": norm(opp.expected_return
                                    if opp.expected_return is not None
                                    else expectancy, s.expectancy_full),
            "hist_expectancy": norm(expectancy, s.expectancy_full),
            "hist_win_rate": norm(win_rate, s.win_rate_full),
            "confidence": clip01(opp.confidence),
            # correlation in [-1, 1] -> [0, 1]; no evidence -> neutral
            "calibration": (NEUTRAL if calibration is None
                            else clip01((calibration + 1.0) / 2.0)),
            "liquidity": norm(opp.liquidity.get("volume_ratio"),
                              s.liquidity_volume_ratio_full),
            "reliability": clip01(n_trades / s.reliability_trades_full)
            if n_trades else NEUTRAL,
            # cost is a PENALTY: 0 cost -> 1.0, cost_full -> 0.0
            "cost": clip01(1.0 - (opp.est_cost_pct or 0.0) / s.cost_full),
            "risk_reward": norm(opp.risk_reward, s.risk_reward_full),
        }
        weight_map = {name: getattr(w, name) for name in components}
        total = sum(weight_map.values())
        if total <= 0:
            return 0.0, components
        score = sum(weight_map[name] * value
                    for name, value in components.items()) / total
        return round(clip01(score), 4), {k: round(v, 4)
                                         for k, v in components.items()}

    # -------------------------------------------------------------- ranking

    def rank(self, opportunities: List) -> List:
        """Order best-first by weighted score; stamp rank/score/breakdown."""
        scored = []
        for opp in opportunities:
            opp.rank_score, opp.rank_breakdown = self.score(opp)
            scored.append(opp)
        scored.sort(key=lambda o: (o.rank_score, o.confidence), reverse=True)
        for i, opp in enumerate(scored, start=1):
            opp.rank = i
        return scored
