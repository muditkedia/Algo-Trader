"""Shared read-side queries over the evidence database.

One home for evidence statistics consumed by more than one layer (the ranking
engine and the research engine), so the SQL exists exactly once.
"""

from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from algo.evidence.database import EvidenceDB


def strategy_overall_stats(db: EvidenceDB) -> Dict[str, dict]:
    """Latest 'overall' evaluation per strategy NAME.

    Returns {name: {win_rate, expectancy, profit_factor, n_trades, status}}.
    Strategies without evaluations are simply absent - consumers must treat
    missing evidence as unknown, not as zero.
    """
    rows = db.connection.execute(
        "SELECT s.name, s.status, e.win_rate, e.net_expectancy AS expectancy, "
        "e.profit_factor, e.n_trades, MAX(e.as_of) AS as_of "
        "FROM evaluations e JOIN strategies s USING (strategy_id) "
        "WHERE e.scope_type = 'overall' GROUP BY s.name").fetchall()
    return {r["name"]: {"win_rate": r["win_rate"],
                        "expectancy": r["expectancy"],
                        "profit_factor": r["profit_factor"],
                        "n_trades": r["n_trades"],
                        "status": r["status"]}
            for r in rows}


def confidence_outcome_correlation(db: EvidenceDB, strategy_id: int,
                                   min_n: int = 10) -> Optional[float]:
    """Spearman correlation (Pearson on ranks) between recorded confidence and
    realized simulated P&L for a strategy; None when evidence is insufficient.

    This is THE calibration statistic (the L-003 question) - used by both the
    research engine's calibration report and the ranking engine.
    """
    rows = db.connection.execute(
        "SELECT s.confidence_score AS conf, o.sim_pnl_net AS pnl "
        "FROM signals s JOIN signal_outcomes o USING (signal_id) "
        "WHERE s.strategy_id = ? AND s.confidence_score IS NOT NULL "
        "AND o.sim_pnl_net IS NOT NULL", (strategy_id,)).fetchall()
    frame = pd.DataFrame([dict(r) for r in rows])
    if len(frame) < min_n or frame["conf"].nunique() < 2:
        return None
    corr = float(frame["conf"].rank().corr(frame["pnl"].rank()))
    return None if corr != corr else corr


def strategy_id_by_name(db: EvidenceDB, name: str) -> Optional[int]:
    row = db.connection.execute(
        "SELECT strategy_id FROM strategies WHERE name = ? "
        "ORDER BY strategy_id DESC LIMIT 1", (name,)).fetchone()
    return int(row[0]) if row else None
