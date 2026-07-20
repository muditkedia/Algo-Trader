"""Factor composite (low-volatility x momentum) - long the top composite decile.

Thesis (Asness et al.; factor-investing practice): combining two imperfectly-
correlated factor signals - low volatility and momentum - yields a more robust
cross-sectional score than either alone. Tests factor COMBINATION: whether a
composite rank survives where the singles may not. Pre-registered in
research/PREREGISTRATION_BATCH2.md (#13).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algo.core.config import from_dict
from algo.core.enums import Direction, HoldingScope
from algo.execution import atr_trail_swing
from algo.strategies.base import StrategyMeta
from algo.strategies.cross_section import (
    CrossSectionalDecileStrategy, composite_percentile, decile_flag,
)


@dataclass(frozen=True)
class ComboParams:
    formation: int = 252
    skip: int = 21
    vol_window: int = 126
    quantile: float = 0.10

    @classmethod
    def from_dict(cls, data) -> "ComboParams":
        return from_dict(cls, data)


class LowVolMomentumComposite(CrossSectionalDecileStrategy):
    metric_col = "combo_pct"
    top = True

    meta = StrategyMeta(
        name="combo_lowvol_mom_daily", version="1.0",
        direction=Direction.LONG, holding_scope=HoldingScope.SWING,
        timeframe="1d", min_bars=280,
        required_columns=("close", "combo_pct", "xs_flag"),
        supported_regimes=("bull",),
        hypothesis=("A composite of momentum percentile and inverse-volatility "
                    "percentile is more robust than either single factor; hold "
                    "the top composite decile. Tests factor combination."),
        expected_behaviour="Favours strong-and-calm names; moderate turnover.",
        known_failure_modes=(
            "if both single factors fail, the composite has nothing to combine",
            "equal weighting is a choice, not an optimum (kept fixed, untuned)",
            "long-only",
        ),
        enabled=True,
        horizon_bars=(21, 63, 126), max_hold_bars=126,
    )

    #: Swing execution owned by this strategy: ATR/structure stop,
    #: chandelier trail + profit locks, overnight allowed, horizon end
    #: at its declared max hold (no session square-off).
    execution = atr_trail_swing(meta.max_hold_bars)

    def __init__(self, settings=None) -> None:
        super().__init__(settings or ComboParams())
        self.quantile = self.settings.quantile

    def min_history(self) -> int:
        p = self.settings
        return max(p.formation + p.skip, p.vol_window) + 10

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.settings
        out = df.copy()
        out["mom_12_1"] = out["close"].shift(p.skip) / out["close"].shift(p.formation) - 1.0
        out["vol"] = out["close"].pct_change().rolling(
            p.vol_window, min_periods=p.vol_window).std()
        return out

    def prepare_cross_section(self, frames):
        # rank(ascending): higher value -> higher percentile. Momentum is
        # higher-is-better (ascending=True); volatility is lower-is-better, so
        # it ranks DESCENDING (ascending=False) to give calm names a high pct.
        composite_percentile(frames, ["mom_12_1", "vol"], "combo_pct",
                             ascending=[True, False])
        decile_flag(frames, "combo_pct", "xs_flag",
                    quantile=self.settings.quantile, top=True)
        return frames
