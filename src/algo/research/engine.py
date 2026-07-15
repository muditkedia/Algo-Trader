"""Research Engine - turns accumulated evidence into strategy verdicts.

This is the heart of the platform. It reads trades/signals from the evidence
store and drives them through the reused validation battery (metrics, Monte
Carlo, walk-forward, sensitivity, stress, portfolio, regime, report), writing
per-scope ``evaluations`` that the confidence layer later reads.

Phase 1 delivers a working skeleton:

  * ``trades_frame``   - pull stored trades into the canonical schema the
                         validation package consumes (reuse, no rewrite).
  * ``validate``       - run the full validation coordinator over a trades
                         source and produce the standardized report.
  * ``evaluate_strategy`` - compute the metric battery for a strategy and
                         persist an ``overall`` evaluation row.
  * ``league_table``   - rank strategies by stored evaluations.

The market-data-dependent parts (direct edge measurement via the ported edge
lab, and outcome labeling of raw signals) are explicit ``NotImplementedError``
placeholders until Phase 2 supplies equity data - the same honest-placeholder
discipline the crypto tooling used (e.g. the Mode B optimizer, delayed-exit
stress).
"""

from __future__ import annotations

from typing import List, Optional, Union
from pathlib import Path

import pandas as pd

from algo.core.config import MarketConfig
from algo.core.costs import CostModel, FlatCostModel
from algo.core.logging import get_logger
from algo.evidence.database import EvidenceDB
from algo.evidence.logger import EvidenceLogger
from algo.research.validation import coordinator, loaders, metrics

logger = get_logger("research.engine")

#: Columns the canonical trades frame needs (validation package contract).
_TRADE_COLUMNS = (
    "pair", "open_date", "close_date", "profit_ratio", "profit_abs",
    "stake_amount", "trade_duration", "exit_reason", "enter_tag",
    "stop_distance_pct",
)


class ResearchEngine:
    """Orchestrates evidence + the validation battery into strategy evidence."""

    def __init__(self, db: EvidenceDB, *, market: Optional[MarketConfig] = None,
                 cost_model: Optional[CostModel] = None) -> None:
        self.db = db
        self.logger_ = EvidenceLogger(db)
        self.market = market or MarketConfig()
        self.cost_model = cost_model or FlatCostModel()

    # ------------------------------------------------------- evidence access

    def trades_frame(self, strategy_id: Optional[int] = None,
                     mode: Optional[str] = None) -> pd.DataFrame:
        """Load stored trades into the canonical validation schema.

        The evidence ``trades`` table uses ``symbol``; the validation package
        expects ``pair``. We alias at this single boundary so the whole battery
        is reused unchanged.
        """
        clauses, params = [], []
        if strategy_id is not None:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        if mode is not None:
            clauses.append("mode = ?")
            params.append(mode)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.connection.execute(
            "SELECT symbol AS pair, open_date, close_date, profit_ratio, "
            "profit_abs, stake_amount, trade_duration, exit_reason, enter_tag, "
            f"stop_distance_pct FROM trades{where}", params
        ).fetchall()
        frame = pd.DataFrame([dict(r) for r in rows],
                             columns=list(_TRADE_COLUMNS))
        if frame.empty:
            return frame
        # If no stored holding time, let the loader derive it from the dates.
        if frame["trade_duration"].isna().all():
            frame = frame.drop(columns=["trade_duration"])
        return loaders.ensure_trades(frame)

    # --------------------------------------------------------- validation run

    def validate(self, trades_source: Union[str, Path, pd.DataFrame],
                 out_report: Union[str, Path],
                 options: Optional[coordinator.ValidationOptions] = None) -> dict:
        """Run the full data-free validation pipeline; returns all results.

        Thin delegation to the reused coordinator - the platform gains the whole
        18-section report + verdict for free.
        """
        return coordinator.run_validation(trades_source, out_report, options)

    def evaluate_strategy(self, strategy_id: int,
                          start_capital: float = 100_000.0,
                          mode: Optional[str] = None,
                          run_id: Optional[int] = None) -> dict:
        """Compute the metric battery for a strategy and store an overall
        evaluation row. Returns the metric summary (empty dict if no trades)."""
        trades = self.trades_frame(strategy_id, mode=mode)
        if trades.empty:
            logger.info("strategy %s has no trades to evaluate", strategy_id)
            self.logger_.record_evaluation(
                strategy_id, "overall", "all", n_trades=0, run_id=run_id,
                verdict="no_trades")
            return {}
        summary = metrics.summarize(trades, start_capital)
        self.logger_.record_evaluation(
            strategy_id, "overall", "all",
            n_trades=summary.get("trades"),
            net_expectancy=summary.get("expectancy"),
            win_rate=summary.get("win_rate"),
            profit_factor=summary.get("profit_factor"),
            run_id=run_id,
        )
        logger.info("evaluated strategy %s: %d trades, PF=%s, expectancy=%s",
                    strategy_id, summary.get("trades"),
                    summary.get("profit_factor"), summary.get("expectancy"))
        return summary

    def league_table(self, scope_type: str = "overall") -> pd.DataFrame:
        """Latest evaluation per strategy for ``scope_type``, ranked.

        Ranked by profit factor then net expectancy (both descending). This is
        the standing strategy comparison; ranking on CI lower bounds (per the
        validation protocol) is layered on once Monte-Carlo CIs are stored.
        """
        rows = self.db.connection.execute(
            "SELECT e.strategy_id, s.name, s.version, s.status, e.scope_value, "
            "e.n_trades, e.net_expectancy, e.profit_factor, e.win_rate, "
            "e.ci_lo, e.wfe, e.verdict, MAX(e.as_of) AS as_of "
            "FROM evaluations e JOIN strategies s USING (strategy_id) "
            "WHERE e.scope_type = ? "
            "GROUP BY e.strategy_id, e.scope_value "
            "ORDER BY e.profit_factor DESC, e.net_expectancy DESC",
            (scope_type,)
        ).fetchall()
        return pd.DataFrame([dict(r) for r in rows])

    # -------------------------------------------------- Phase-2 placeholders

    def measure_edge(self, *args, **kwargs):
        """Direct forward-return edge measurement (the D-007 gate).

        Requires equity market data and the ported edge lab; deliberately not
        implemented until Phase 2 (research-engine port).
        """
        raise NotImplementedError(
            "measure_edge requires market data + the ported edge lab (Phase 2). "
            "It measures forward returns/MFE/MAE net of the cost model and "
            "hurdles the edge against >= 2x round-trip cost before a strategy "
            "may advance past MEASURED."
        )

    def label_outcomes(self, *args, **kwargs):
        """Fill signal_outcomes for matured signals from market data.

        Requires the market-data store; not implemented until Phase 2.
        """
        raise NotImplementedError(
            "label_outcomes requires the market-data store (Phase 2). It "
            "computes forward returns, MFE/MAE, gap exposure, cost, and the "
            "simulated exit for every signal whose horizons have matured."
        )
