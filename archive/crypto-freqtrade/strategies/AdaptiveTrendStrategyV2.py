"""AdaptiveTrendStrategyV2 - evidence-driven redesign (Phases D/E).

PRESERVED from v1 (proven valuable / structural): the MTF pipeline, indicator
calculations, mandatory GO architecture, hard stop, position sizing, the
trailing-stop MECHANISM, and the validation tooling.

REDESIGNED (proven ineffective in Phases D/E):
  * objective exits   -> 5m trend-reversal exit (was 1h EMA-cross, ~225min lag)
  * trailing tuning   -> tighter trail + earlier activation + higher locks
  * advisory scoring  -> removed (winner score == loser score)
  * risk_reward gate  -> removed (near-constant, uncorrelated with outcome)
  * anti-chase filter -> added (reject extended entries)
  * cost gate         -> added (expected reward > 2x round-trip cost)

v1 (AdaptiveTrendStrategy) is left completely intact; both classes load side by
side. v2 is a thin subclass that reuses all of v1's pipeline and swaps in the
v2 components, so no v1 code path changes.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

_STRATEGY_DIR = str(Path(__file__).parent)
if _STRATEGY_DIR not in sys.path:
    sys.path.append(_STRATEGY_DIR)

from AdaptiveTrendStrategy import AdaptiveTrendStrategy

from algo_core.decision_engine import RejectionRecorder
from algo_core.decision_engine_v2 import DecisionEngineV2
from algo_core.profiles.trend_following_v2 import TrendFollowingV2Profile
from algo_core.settings_v2 import V2Settings
from algo_core.trade_manager_v2 import TradeManagerV2

logger = logging.getLogger(__name__)


class AdaptiveTrendStrategyV2(AdaptiveTrendStrategy):

    def __init__(self, config: dict) -> None:
        # Build v1 first (pipeline, settings, informative setups, startup count,
        # regime detector), then swap in the v2 components.
        super().__init__(config)
        self.v2settings = V2Settings.from_config(config)
        v2_risk = self.v2settings.risk_params()

        self.trade_manager = TradeManagerV2(v2_risk)
        self.profile = TrendFollowingV2Profile(
            settings=self.settings, trade_manager=self.trade_manager,
            v2=self.v2settings.v2)

        user_data_dir = config.get("user_data_dir")
        rejection_path = (
            Path(user_data_dir) / "logs" / "trade_rejections_v2.jsonl"
            if user_data_dir else None)
        self.decision_engine = DecisionEngineV2(
            self.settings.engine, v2_risk, self.v2settings.v2,
            recorder=RejectionRecorder(rejection_path))
        logger.info(
            "AdaptiveTrendStrategyV2 wired: 5m-reversal exits, tighter trail "
            "(x%.2f ATR @ +%.1f%%), anti-chase + cost gate; profile=%s",
            self.v2settings.v2.trail_atr_multiplier,
            self.v2settings.v2.trail_activation_profit * 100, self.profile.name)

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe = super().populate_indicators(dataframe, metadata)
        # anti-chase features (backward-looking; used by the profile + engine)
        dataframe["mom_1h"] = dataframe["close"] / dataframe["close"].shift(12) - 1
        dataframe["dist_fast_5m"] = (
            (dataframe["close"] - dataframe["ema_fast"]) / dataframe["close"])
        return dataframe
