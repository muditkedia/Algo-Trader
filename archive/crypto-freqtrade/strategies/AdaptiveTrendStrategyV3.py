"""AdaptiveTrendStrategyV3 - controlled exit refinement (single-variable vs v2).

Keeps EVERYTHING from v2 (anti-chase, cost gate, tighter trailing, removed
advisory scoring + RR gate) and changes only the structural exit: v2's 5m
EMA-cross exit is replaced by a confirmation-based 15m exit (see
trade_manager_v3.py). This isolates whether v2's catastrophe was caused
specifically by the 5m execution-timeframe exit.

v1 and v2 are left completely intact; all three strategies load side by side.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_STRATEGY_DIR = str(Path(__file__).parent)
if _STRATEGY_DIR not in sys.path:
    sys.path.append(_STRATEGY_DIR)

from AdaptiveTrendStrategyV2 import AdaptiveTrendStrategyV2

from algo_core.decision_engine import RejectionRecorder
from algo_core.decision_engine_v2 import DecisionEngineV2
from algo_core.trade_manager_v3 import TradeManagerV3

logger = logging.getLogger(__name__)


class AdaptiveTrendStrategyV3(AdaptiveTrendStrategyV2):

    def __init__(self, config: dict) -> None:
        # Build v2 (which builds v1), then swap ONLY the structural exit.
        super().__init__(config)
        v2_risk = self.v2settings.risk_params()   # identical tighter trailing

        self.trade_manager = TradeManagerV3(v2_risk)
        # the profile delegates exit_signal to its trade_manager
        self.profile.trade_manager = self.trade_manager

        user_data_dir = config.get("user_data_dir")
        rejection_path = (
            Path(user_data_dir) / "logs" / "trade_rejections_v3.jsonl"
            if user_data_dir else None)
        # engine is identical to v2 (anti-chase + cost); only the log path differs
        self.decision_engine = DecisionEngineV2(
            self.settings.engine, v2_risk, self.v2settings.v2,
            recorder=RejectionRecorder(rejection_path))
        logger.info(
            "AdaptiveTrendStrategyV3 wired: 15m confirmed-reversal structural "
            "exit; v2 trailing + anti-chase + cost preserved")
