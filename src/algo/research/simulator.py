"""Trade simulator - bar replay of a managed trade, producing canonical trades.

Reuses the promoted risk engine directly (``initial_stop_pct``,
``trailing_stop_price``) and the archived TradeManager's stop-management
ALGORITHM: the working stop is the running MAXIMUM of {initial stop, unlocked
profit-lock tiers, ATR trail} - monotonic by construction, never widened. The
TradeManager class itself could not be reused verbatim because its
``stoploss_ratio`` is bound to a Freqtrade ``Trade`` object (custom-data
persistence); the logic it implemented is reproduced here over stored bars.

Exits supported (deliberately only these):
  * ``stop_loss``        - the initial ATR/structure stop is hit
  * ``trailing_stop``    - the ratcheted trail/profit-lock stop is hit
  * ``session_squareoff``- intraday (MIS) positions closed at the session's last
                           bar, which is a market rule, not a strategy choice
  * ``horizon_end``      - ran out of bars (swing trades)

No structural/indicator exits: D-006 removed them after all three crypto
variants produced a 0% win rate. Costs are priced through the injected
``CostModel``, so every simulated P&L is NET.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from algo.core.costs import CostModel, Product
from algo.core.logging import get_logger
from algo.evidence.models import TradeRecord
from algo.risk.engine import RiskParams, initial_stop_pct, trailing_stop_price

logger = get_logger("research.simulator")


@dataclass
class SimulatedTrade:
    """Outcome of one simulated trade (superset of the canonical schema)."""

    symbol: str
    open_date: pd.Timestamp
    close_date: pd.Timestamp
    entry_price: float
    exit_price: float
    exit_reason: str
    holding_min: float
    stop_distance_pct: float
    quantity: float
    stake_amount: float
    gross_ratio: float          # before costs
    cost_ratio: float           # round-trip cost as a fraction of entry notional
    profit_ratio: float         # NET
    profit_abs: float           # NET, account currency
    mfe_pct: float              # best excursion while open
    mae_pct: float              # worst excursion while open

    def to_record(self, strategy_id: Optional[int] = None,
                  signal_id: Optional[int] = None,
                  mode: str = "backtest",
                  enter_tag: str = "") -> TradeRecord:
        """Canonical trade output (evidence trades table + validation schema)."""
        return TradeRecord(
            mode=mode, symbol=self.symbol,
            open_date=str(self.open_date), close_date=str(self.close_date),
            profit_ratio=self.profit_ratio, profit_abs=self.profit_abs,
            stake_amount=self.stake_amount, trade_duration=self.holding_min,
            exit_reason=self.exit_reason, enter_tag=enter_tag,
            stop_distance_pct=self.stop_distance_pct,
            signal_id=signal_id, strategy_id=strategy_id)


def simulate_trade(
    bars: pd.DataFrame,
    entry_index: int,
    *,
    atr: float,
    swing_low: Optional[float],
    params: RiskParams,
    cost_model: CostModel,
    product: Product,
    stake: float = 100_000.0,
    max_bars: Optional[int] = None,
) -> Optional[SimulatedTrade]:
    """Replay a long trade entered at ``bars[entry_index]`` close.

    Intrabar convention (conservative, matching the crypto harness): the STOP is
    checked against the bar's low before any favourable excursion is credited,
    so a bar that both stops out and rallies is recorded as a stop-out.
    """
    if entry_index >= len(bars) - 1:
        return None
    entry = bars.iloc[entry_index]
    entry_price = float(entry["close"])
    stop_pct = initial_stop_pct(entry_price, atr, swing_low, params)
    if stop_pct is None:
        return None
    stop_pct = min(stop_pct, params.hard_stop_pct)
    stop_price = entry_price * (1.0 - stop_pct)
    trailed = False          # True once the stop has ratcheted above its start

    future = bars.iloc[entry_index + 1:]
    if max_bars is not None:
        future = future.iloc[:max_bars]
    if future.empty:
        return None

    entry_day = pd.Timestamp(entry["date"]).normalize()
    intraday = product == Product.INTRADAY
    mfe = mae = 0.0
    exit_price = float(future.iloc[-1]["close"])
    exit_reason = "horizon_end"
    exit_date = pd.Timestamp(future.iloc[-1]["date"])

    for i in range(len(future)):
        bar = future.iloc[i]
        bar_date = pd.Timestamp(bar["date"])
        low, high, close = float(bar["low"]), float(bar["high"]), float(bar["close"])

        # 1) stop first (conservative)
        if low <= stop_price:
            exit_price = stop_price
            exit_reason = "trailing_stop" if trailed else "stop_loss"
            exit_date = bar_date
            mae = min(mae, stop_price / entry_price - 1.0)
            break

        mfe = max(mfe, high / entry_price - 1.0)
        mae = min(mae, low / entry_price - 1.0)

        # 2) session square-off for intraday (market rule)
        if intraday:
            last_of_session = (
                i == len(future) - 1
                or pd.Timestamp(future.iloc[i + 1]["date"]).normalize()
                != bar_date.normalize()
            )
            if last_of_session and bar_date.normalize() >= entry_day:
                exit_price, exit_reason, exit_date = close, "session_squareoff", bar_date
                break

        # 3) ratchet the stop (monotonic - archived TradeManager algorithm)
        current_profit = close / entry_price - 1.0
        candidate = trailing_stop_price(entry_price, close, current_profit,
                                        atr, params)
        if candidate is not None:
            raised = min(candidate, close * (1 - 1e-4))
            if raised > stop_price:
                stop_price, trailed = raised, True

        if i == len(future) - 1:
            exit_price, exit_reason, exit_date = close, "horizon_end", bar_date

    quantity = max(stake / entry_price, 1e-9)
    gross_ratio = exit_price / entry_price - 1.0
    cost_ratio = cost_model.round_trip_pct(
        entry_price=entry_price, exit_price=exit_price, quantity=quantity,
        product=product)
    profit_ratio = gross_ratio - cost_ratio
    holding_min = (exit_date - pd.Timestamp(entry["date"])).total_seconds() / 60.0

    return SimulatedTrade(
        symbol=str(entry.get("symbol", "")),
        open_date=pd.Timestamp(entry["date"]), close_date=exit_date,
        entry_price=entry_price, exit_price=exit_price,
        exit_reason=exit_reason, holding_min=holding_min,
        stop_distance_pct=stop_pct, quantity=quantity,
        stake_amount=entry_price * quantity,
        gross_ratio=gross_ratio, cost_ratio=cost_ratio,
        profit_ratio=profit_ratio, profit_abs=profit_ratio * entry_price * quantity,
        mfe_pct=mfe, mae_pct=mae)
