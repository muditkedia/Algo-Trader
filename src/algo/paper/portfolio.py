"""Portfolio manager - decides OPEN / SKIP / REDUCE / REPLACE per opportunity.

Before any position opens, the manager evaluates the CURRENT portfolio state:
available capital, deployed exposure, position count, sector concentration
(sectors from the evidence instruments table; same-sector positions are also
the correlation proxy until return-correlation evidence exists - documented,
not hidden), the remaining daily risk budget, and how the new opportunity
compares with what is already held.

Decisions (all thresholds configurable, nothing hardcoded):

    OPEN     capacity + budget + caps all clear (size may still scale).
    REDUCE   soft limits bind (sector already represented, or the risk budget
             is more than half spent) - open at a reduced size multiplier.
    REPLACE  book is full BUT the new opportunity out-scores the weakest open
             position by the configured margin - close the weakest, open new.
    SKIP     hard limits bind (capital, budget exhausted, sector cap, or the
             new opportunity is not better than what it would displace).

Every decision (including SKIPs) is returned with its reason so the paper
engine can record it to evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from algo.core.config import from_dict
from algo.core.logging import get_logger

logger = get_logger("paper.portfolio")


class Action(str, Enum):
    OPEN = "open"
    SKIP = "skip"
    REDUCE = "reduce"
    REPLACE = "replace"


@dataclass(frozen=True)
class PortfolioConfig:
    total_capital: float = 1_000_000.0
    max_open_positions: int = 5
    #: max fraction of capital deployed across all open positions
    max_capital_deployed: float = 0.60
    #: max simultaneous positions in one sector (correlation proxy)
    max_positions_per_sector: int = 2
    #: size multiplier applied when soft limits bind (REDUCE)
    reduce_multiplier: float = 0.5
    #: total stop-loss exposure allowed per day, fraction of capital
    daily_risk_budget: float = 0.02
    #: REDUCE (instead of full size) once this fraction of the budget is spent
    risk_budget_soft_pct: float = 0.5
    #: a replacement must out-score the weakest holding by this margin
    replace_min_improvement: float = 0.15

    @classmethod
    def from_dict(cls, data) -> "PortfolioConfig":
        return from_dict(cls, data)


@dataclass
class PortfolioState:
    """Snapshot of the book the manager decides against."""

    open_positions: int = 0
    capital_deployed: float = 0.0
    #: symbol -> quality score of each open position (rank_score or confidence)
    position_scores: Dict[str, float] = field(default_factory=dict)
    #: symbol -> sector (from the evidence instruments table; None = unknown)
    position_sectors: Dict[str, Optional[str]] = field(default_factory=dict)
    #: risk already committed today: sum(stake x stop_pct) + realized losses
    risk_spent_today: float = 0.0


@dataclass
class Decision:
    action: Action
    reason: str
    size_multiplier: float = 1.0
    replace_symbol: Optional[str] = None

    @property
    def opens(self) -> bool:
        return self.action in (Action.OPEN, Action.REDUCE, Action.REPLACE)


class PortfolioManager:
    def __init__(self, config: Optional[PortfolioConfig] = None,
                 sector_fn=None) -> None:
        """``sector_fn(symbol) -> Optional[str]`` supplies sector membership
        (the paper engine wires it to the evidence instruments table)."""
        self.config = config or PortfolioConfig()
        self.sector_fn = sector_fn or (lambda symbol: None)

    # ------------------------------------------------------------- evaluate

    def evaluate(self, opp, state: PortfolioState,
                 available_capital: float) -> Decision:
        cfg = self.config
        quality = opp.rank_score if opp.rank_score is not None \
            else opp.confidence

        # --- hard: capital -------------------------------------------------
        if available_capital <= 0:
            return Decision(Action.SKIP, "no available capital")
        if state.capital_deployed >= cfg.total_capital * cfg.max_capital_deployed:
            return Decision(Action.SKIP,
                            f"deployed capital at cap "
                            f"({cfg.max_capital_deployed:.0%} of capital)")

        # --- hard: daily risk budget ---------------------------------------
        budget = cfg.total_capital * cfg.daily_risk_budget
        budget_left = budget - state.risk_spent_today
        if budget_left <= 0:
            return Decision(Action.SKIP, "daily risk budget exhausted")

        # --- sector / correlation proxy ------------------------------------
        sector = self.sector_fn(opp.symbol)
        reduce_reasons: List[str] = []
        if sector is not None:
            same_sector = sum(1 for s in state.position_sectors.values()
                              if s == sector)
            if same_sector >= cfg.max_positions_per_sector:
                return Decision(Action.SKIP,
                                f"sector cap: already {same_sector} open in "
                                f"{sector}")
            if same_sector >= 1:
                reduce_reasons.append(
                    f"correlated exposure: {same_sector} open in {sector}")

        # --- soft: risk budget more than half spent -------------------------
        if state.risk_spent_today >= budget * cfg.risk_budget_soft_pct:
            reduce_reasons.append(
                f"risk budget {state.risk_spent_today / budget:.0%} spent")

        # --- book full: consider replacement --------------------------------
        if state.open_positions >= cfg.max_open_positions:
            if not state.position_scores:
                return Decision(Action.SKIP, "book full")
            weakest_symbol = min(state.position_scores,
                                 key=state.position_scores.get)
            weakest_score = state.position_scores[weakest_symbol]
            if quality >= weakest_score + cfg.replace_min_improvement:
                return Decision(
                    Action.REPLACE,
                    f"book full; new score {quality:.2f} beats weakest "
                    f"{weakest_symbol} ({weakest_score:.2f}) by >= "
                    f"{cfg.replace_min_improvement}",
                    size_multiplier=(cfg.reduce_multiplier if reduce_reasons
                                     else 1.0),
                    replace_symbol=weakest_symbol)
            return Decision(Action.SKIP,
                            f"book full; new score {quality:.2f} does not "
                            f"beat weakest ({weakest_score:.2f}) by the "
                            "required margin")

        # --- open (possibly reduced) ----------------------------------------
        if reduce_reasons:
            return Decision(Action.REDUCE, "; ".join(reduce_reasons),
                            size_multiplier=cfg.reduce_multiplier)
        return Decision(Action.OPEN, "capacity and budget clear")
