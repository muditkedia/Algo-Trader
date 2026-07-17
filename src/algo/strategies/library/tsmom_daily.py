"""Time-Series Momentum - a stock's own 12-month trend predicts its next months.

Thesis (Moskowitz/Ooi/Pedersen 2012; Asness et al. 2013): under-reaction and
slow information diffusion make the 12-month return (skipping the most recent
month, which mean-reverts) persist over the following 1-3 months. The most
replicated effect in the time-series literature; India-specific factor evidence
exists (IIMA library). Pre-registered in research/PREREGISTRATION_BATCH1.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.core.indicators import crossed_above
from algo.strategies.base import StrategyMeta, StrategyProfile
from algo.strategies.confidence import Component, ConfidenceScore, clip01, weighted


@dataclass(frozen=True)
class TsMomParams:
    formation: int = 252        # ~12 months of sessions
    skip: int = 21              # skip the most recent month
    threshold: float = 0.0      # sign rule - deliberately parameter-free

    @classmethod
    def from_dict(cls, data) -> "TsMomParams":
        return from_dict(cls, data)


class TimeSeriesMomentum(StrategyProfile):

    meta = StrategyMeta(
        name="tsmom_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=280,
        required_columns=("close", "momentum"),
        supported_regimes=("bull",),
        hypothesis=("A stock's own 12-month return (skipping the latest month) "
                    "persists over the next 1-3 months: investors under-react "
                    "and information diffuses slowly, so an established own-"
                    "trend continues beyond the point costs are covered."),
        expected_behaviour=("Fires on the TRANSITION of 12-1 momentum to "
                            "positive - a few signals per stock per cycle; the "
                            "slowest-turning strategy in the batch."),
        known_failure_modes=(
            "momentum crash: sharp bear-market rebounds invert the signal",
            "whipsaw around zero in flat markets fires repeated weak entries",
            "3.5y of data holds ~3 independent 12-month formation periods",
        ),
        enabled=True,
        horizon_bars=(5, 10, 20, 40, 60), max_hold_bars=60,
    )

    def __init__(self, settings=None) -> None:
        super().__init__(settings or TsMomParams())

    def min_history(self) -> int:
        p = self.settings
        return p.formation + p.skip + 5

    def prepare(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        df = dataframe.copy()
        # 12-1 momentum: return from t-252 to t-21. Both legs lagged - nothing
        # in the most recent month enters the signal.
        df["momentum"] = df["close"].shift(p.skip) / df["close"].shift(p.formation) - 1.0
        return df

    def entry_signal(self, dataframe: pd.DataFrame) -> pd.Series:
        if self.missing_columns(dataframe) or len(dataframe) < self.min_history():
            return self.no_signal(dataframe)
        p = self.settings
        threshold = pd.Series(p.threshold, index=dataframe.index)
        return crossed_above(dataframe["momentum"], threshold).fillna(False)

    def confidence(self, dataframe: pd.DataFrame) -> ConfidenceScore:
        if self.missing_columns(dataframe) or dataframe.empty:
            return ConfidenceScore.zero("missing columns")
        momentum = float(dataframe["momentum"].iloc[-1])
        # strength: how far past the threshold the formation return is
        strength = clip01(momentum / 0.30)
        return weighted(
            [Component("momentum_strength", strength, 1.0,
                       f"12-1 momentum {momentum:+.1%}")],
            reason="own 12-month trend turned positive")
