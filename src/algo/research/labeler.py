"""Outcome Labeler - fills signal_outcomes for matured signals.

For every recorded signal whose forward window has now completed, this computes
what actually happened after it - forward returns, MFE/MAE, overnight gap,
modelled cost, and the simulated managed-trade result - and writes one
``signal_outcomes`` row. Signals whose horizons have NOT matured are left alone
(re-runs pick them up later), and signals already labelled are skipped, so the
labeler is idempotent.

This closes the evidence loop the whole platform is built around: signals are
recorded whether traded or not, outcomes are attached once knowable, and the
research engine measures the pair.
"""

from __future__ import annotations

from typing import Dict, Optional, Sequence

import pandas as pd

from algo.core.costs import CostModel, Product
from algo.core.indicators import atr as atr_series
from algo.core.logging import get_logger
from algo.data.ohlcv import timeframe_minutes
from algo.evidence.models import SignalOutcome
from algo.research.simulator import simulate_trade
from algo.risk.engine import RiskParams

logger = get_logger("research.labeler")

#: Which horizon columns are meaningful per timeframe (minutes -> column).
_INTRADAY_HORIZONS = {15: "ret_15m", 30: "ret_30m", 60: "ret_60m",
                      120: "ret_120m"}
_DAILY_HORIZONS = {1: "ret_1d", 3: "ret_3d", 5: "ret_5d"}


class OutcomeLabeler:
    def __init__(self, store, evidence_logger, cost_model: CostModel,
                 risk_params: Optional[RiskParams] = None,
                 atr_period: int = 14, swing_window: int = 10) -> None:
        self.store = store
        self.logger_ = evidence_logger
        self.cost_model = cost_model
        self.risk_params = risk_params or RiskParams()
        self.atr_period = atr_period
        self.swing_window = swing_window

    # ------------------------------------------------------------ selection

    def pending(self, strategy_id: int) -> pd.DataFrame:
        """Signals for a strategy that have no outcome row yet."""
        rows = self.logger_.db.connection.execute(
            "SELECT s.signal_id, s.symbol, s.ts, s.entry_price "
            "FROM signals s LEFT JOIN signal_outcomes o "
            "ON o.signal_id = s.signal_id "
            "WHERE s.strategy_id = ? AND o.signal_id IS NULL",
            (strategy_id,)).fetchall()
        return pd.DataFrame([dict(r) for r in rows])

    # -------------------------------------------------------------- labeling

    def label_strategy(self, strategy_id: int, timeframe: str,
                       product: Product = Product.INTRADAY,
                       max_hold_bars: int = 8) -> int:
        """Label every matured signal for ``strategy_id``; returns rows written."""
        todo = self.pending(strategy_id)
        if todo.empty:
            return 0
        tf_min = timeframe_minutes(timeframe)
        horizons = (_DAILY_HORIZONS if timeframe == "1d" else _INTRADAY_HORIZONS)
        written = 0
        pending: list = []
        cache: Dict[str, pd.DataFrame] = {}
        # date -> row position, built ONCE per symbol. Scanning the frame for
        # each signal's timestamp instead makes labeling O(signals x bars) -
        # ~1.3 billion comparisons for 62k signals over 21k bars, which is what
        # made the first real run intractable.
        index_cache: Dict[str, dict] = {}

        for row in todo.itertuples(index=False):
            frame = cache.get(row.symbol)
            if frame is None:
                frame = self.store.read(row.symbol, timeframe)
                if not frame.empty:
                    frame = frame.copy()
                    frame["atr"] = atr_series(frame, self.atr_period)
                    frame["swing_low"] = frame["low"].rolling(
                        self.swing_window, min_periods=1).min()
                cache[row.symbol] = frame
                index_cache[row.symbol] = {
                    ts: i for i, ts in enumerate(frame["date"])}
            if frame.empty:
                continue
            outcome = self._label_one(row, frame, timeframe, tf_min, horizons,
                                      product, max_hold_bars,
                                      index_cache[row.symbol])
            if outcome is not None:
                pending.append(outcome)
                if len(pending) >= 2000:          # batch the disk syncs
                    written += self.logger_.record_outcomes(pending)
                    pending = []
        written += self.logger_.record_outcomes(pending)
        logger.info("labeled %d signals for strategy %s", written, strategy_id)
        return written

    def _label_one(self, row, frame: pd.DataFrame, timeframe: str,
                   tf_min: float, horizons: dict, product: Product,
                   max_hold_bars: int,
                   positions: Optional[dict] = None) -> Optional[SignalOutcome]:
        ts = pd.Timestamp(row.ts)
        if positions is not None:
            i = positions.get(ts)
            if i is None:
                return None
        else:
            matches = frame.index[frame["date"] == ts]
            if len(matches) == 0:
                return None
            i = int(matches[0])
        closes = frame["close"].to_numpy(float)
        entry = float(closes[i])

        max_bars = max(list(horizons) if timeframe == "1d"
                       else [int(h / tf_min) for h in horizons if h >= tf_min])
        max_bars = max(max_bars, max_hold_bars)
        if i + max_bars >= len(frame):
            return None                       # not matured yet - try again later

        outcome = SignalOutcome(signal_id=int(row.signal_id))

        # forward returns at the horizons meaningful for this timeframe
        for key, column in horizons.items():
            bars = key if timeframe == "1d" else int(key / tf_min)
            if bars < 1 or i + bars >= len(frame):
                continue
            setattr(outcome, column, float(closes[i + bars] / entry - 1.0))

        # end-of-session return (intraday only)
        if timeframe != "1d":
            day = frame["date"].dt.normalize()
            same = frame.index[(day == day.iloc[i]) & (frame.index > i)]
            if len(same):
                outcome.ret_eod = float(closes[int(same[-1])] / entry - 1.0)

        # excursions over the forward window
        window = frame.iloc[i + 1:i + 1 + max_bars]
        outcome.mfe_pct = float(window["high"].max() / entry - 1.0)
        outcome.mae_pct = float(window["low"].min() / entry - 1.0)

        # overnight gap into the next session
        day = frame["date"].dt.normalize()
        later = frame.index[day > day.iloc[i]]
        if len(later):
            nxt = int(later[0])
            session_close = float(closes[nxt - 1])
            outcome.overnight_gap_pct = float(
                frame["open"].iloc[nxt] / session_close - 1.0)

        # modelled cost + the simulated managed trade
        qty = max(100_000.0 / entry, 1e-9)
        outcome.cost_model_version = type(self.cost_model).__name__
        outcome.est_cost_pct = self.cost_model.round_trip_pct(
            entry_price=entry, exit_price=entry, quantity=qty, product=product)

        sim = simulate_trade(
            frame, i, atr=float(frame["atr"].iloc[i]),
            swing_low=float(frame["swing_low"].iloc[i]),
            params=self.risk_params, cost_model=self.cost_model,
            product=product, max_bars=max_hold_bars)
        if sim is not None:
            outcome.sim_exit_price = sim.exit_price
            outcome.sim_exit_reason = sim.exit_reason
            outcome.sim_holding_min = sim.holding_min
            outcome.sim_pnl_net = sim.profit_ratio
            # exit quality: share of the favourable excursion actually captured
            outcome.exit_quality = (float(sim.gross_ratio / sim.mfe_pct)
                                    if sim.mfe_pct and sim.mfe_pct > 0 else 0.0)
        return outcome
