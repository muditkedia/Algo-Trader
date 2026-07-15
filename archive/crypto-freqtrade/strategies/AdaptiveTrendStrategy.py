"""AdaptiveTrendStrategy - Freqtrade entry point for the Algo Trader framework.

This class is a thin orchestrator; all trading logic lives in algo_core:

    settings              -> algo_core.settings (single source of truth)
    populate_indicators   -> algo_core.indicators (5m base + 15m/1h/4h informative)
    populate_entry_trend  -> active StrategyProfile (vectorized mandatory gate)
    confirm_trade_entry   -> RegimeDetector routing + DecisionEngine GO / NO-GO
    custom_stake_amount   -> risk_engine (risk-based sizing, opt-in)
    custom_stoploss       -> TradeManager (ATR/structure stop, profit locking)
    populate_exit_trend   -> TradeManager objective exits (no time exits)

Safety posture: dry-run config, hard emergency stop -6%, stops only ever
tighten, no minimal_roi, no time-based exits. See architecture/ARCHITECTURE.md.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

# Make algo_core importable regardless of how the resolver loads this file.
_STRATEGY_DIR = str(Path(__file__).parent)
if _STRATEGY_DIR not in sys.path:
    sys.path.append(_STRATEGY_DIR)

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, merge_informative_pair

from algo_core.decision_engine import DecisionEngine, RejectionRecorder, TradeContext
from algo_core.indicators import add_core_indicators
from algo_core.profiles import get_active_profile
from algo_core.regime import RegimeDetector
from algo_core.risk_engine import initial_stop_pct, risk_based_stake
from algo_core.settings import AlgoSettings
from algo_core.trade_manager import TradeManager

logger = logging.getLogger(__name__)

#: Informative timeframes and the columns merged into the 5m base frame.
#: EMA periods are taken from settings at runtime; only structure is fixed here.
_INFORMATIVE_COLUMNS = {
    "15m": ("date", "close", "ema_fast", "ema_slow", "adx", "rsi",
            "atr", "atr_pct", "swing_low", "structure_up"),
    "1h": ("date", "close", "ema_fast", "ema_slow", "adx", "closing_higher"),
    "4h": ("date", "close", "ema_fast", "ema_slow"),
}


class AdaptiveTrendStrategy(IStrategy):

    INTERFACE_VERSION = 3

    timeframe = "5m"
    can_short = False
    process_only_new_candles = True
    # Overridden in __init__ with a value derived from the indicator periods so
    # every informative timeframe (notably the 4h EMA50) is fully warmed up.
    startup_candle_count = 400

    # Hard emergency stop (absolute floor). Working stops are managed
    # dynamically by the TradeManager and only ever tighten.
    stoploss = -0.06
    use_custom_stoploss = True
    trailing_stop = False

    # No ROI table and no time exits: positions are held while objective
    # trend-strength criteria remain valid.
    minimal_roi = {}
    use_exit_signal = True
    exit_profit_only = False
    ignore_buying_expired_candle_after = 300

    position_adjustment_enable = False

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.settings = AlgoSettings.from_config(config)

        # (timeframe, ema_fast, ema_slow, columns) built from settings so
        # indicator periods have exactly one source of truth.
        ind = self.settings.indicators
        self.informative_setups = (
            ("15m", ind.ema_15m_fast, ind.ema_15m_slow, _INFORMATIVE_COLUMNS["15m"]),
            ("1h", ind.ema_1h_fast, ind.ema_1h_slow, _INFORMATIVE_COLUMNS["1h"]),
            ("4h", ind.ema_4h_fast, ind.ema_4h_slow, _INFORMATIVE_COLUMNS["4h"]),
        )
        self.startup_candle_count = self.settings.startup_candles(
            self.timeframe, tuple(s[0] for s in self.informative_setups)
        )

        self.trade_manager = TradeManager(self.settings.risk)
        self.regime_detector = RegimeDetector(self.settings.engine)
        self.profile = get_active_profile(
            self.settings.active_profile,
            settings=self.settings,
            trade_manager=self.trade_manager,
        )

        user_data_dir = config.get("user_data_dir")
        rejection_path = (
            Path(user_data_dir) / "logs" / "trade_rejections.jsonl"
            if user_data_dir else None
        )
        self.decision_engine = DecisionEngine(
            self.settings.engine,
            self.settings.risk,
            recorder=RejectionRecorder(rejection_path),
        )
        logger.info(
            "AdaptiveTrendStrategy initialized - profile=%s, startup_candle_count=%d, "
            "risk_sizing=%s",
            self.profile.name, self.startup_candle_count,
            self.settings.risk.enable_risk_sizing,
        )

    # ------------------------------------------------------------ indicators

    def _add_indicators(self, dataframe: pd.DataFrame, ema_fast: int, ema_slow: int):
        ind = self.settings.indicators
        return add_core_indicators(
            dataframe, ema_fast=ema_fast, ema_slow=ema_slow,
            adx_period=ind.adx_period, rsi_period=ind.rsi_period,
            atr_period=ind.atr_period, volume_window=ind.volume_window,
            structure_window=ind.structure_window, trend_shift=ind.trend_shift,
        )

    def informative_pairs(self):
        pairs = self.dp.current_whitelist() if self.dp else []
        return [
            (pair, setup[0]) for pair in pairs for setup in self.informative_setups
        ]

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        ind = self.settings.indicators
        dataframe = self._add_indicators(dataframe, ind.base_ema_fast, ind.base_ema_slow)
        if not self.dp:
            return dataframe
        for tf, ema_fast, ema_slow, columns in self.informative_setups:
            informative = self.dp.get_pair_dataframe(metadata["pair"], tf)
            if informative is None or informative.empty:
                logger.warning(
                    "No %s informative data for %s - profile will emit no signals.",
                    tf, metadata["pair"],
                )
                continue
            # Copy: get_pair_dataframe may return cached frames we must not mutate.
            informative = self._add_indicators(informative.copy(), ema_fast, ema_slow)
            dataframe = merge_informative_pair(
                dataframe, informative[list(columns)], self.timeframe, tf, ffill=True
            )
        return dataframe

    # --------------------------------------------------------------- signals

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_tag"] = ""
        signal = self.profile.entry_signal(dataframe)
        dataframe.loc[signal, "enter_long"] = 1
        dataframe.loc[signal, "enter_tag"] = self.profile.name
        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_tag"] = ""
        flags, tags = self.profile.exit_signal(dataframe)
        dataframe.loc[flags, "exit_long"] = 1
        dataframe.loc[flags, "exit_tag"] = tags[flags]
        return dataframe

    # ------------------------------------------------------------ GO / NO-GO

    def confirm_trade_entry(
        self, pair: str, order_type: str, amount: float, rate: float,
        time_in_force: str, current_time: datetime, entry_tag: Optional[str],
        side: str, **kwargs,
    ) -> bool:
        row = self._latest_row(pair)
        if row is None:
            logger.warning("No analyzed candle for %s - rejecting entry.", pair)
            return False

        # Regime routing: a profile only trades regimes it declares support for.
        regime = self.regime_detector.detect(row)
        if regime.value not in self.profile.supported_regimes:
            logger.info(
                "Regime %s unsupported by profile %s - skipping %s.",
                regime.value, self.profile.name, pair,
            )
            return False

        ctx = TradeContext(
            pair=pair,
            side=side,
            row=row,
            stake_amount=amount * rate,
            spread_pct=self._current_spread(pair),
            regime=regime.value,
        )
        decision = self.decision_engine.evaluate(ctx, source="confirm_trade_entry")
        if decision.go:
            logger.info(
                "GO %s (regime=%s, score %.2f, tag %s)",
                pair, regime.value, decision.score, entry_tag,
            )
        return decision.go

    # ------------------------------------------------------- risk management

    def custom_stake_amount(
        self, pair: str, current_time: datetime, current_rate: float,
        proposed_stake: float, min_stake: Optional[float], max_stake: float,
        leverage: float, entry_tag: Optional[str], side: str, **kwargs,
    ) -> float:
        # Opt-in: disabled by default, so sizing is the fixed config stake.
        # Enabling also requires config "stake_amount": "unlimited".
        if not self.settings.risk.enable_risk_sizing:
            return proposed_stake
        row = self._latest_row(pair)
        if row is None:
            return proposed_stake
        stop_pct = initial_stop_pct(
            row.get("close"), row.get("atr_15m"), row.get("swing_low_15m"),
            self.settings.risk,
        )
        try:
            available = self.wallets.get_total_stake_amount()
        except Exception:  # wallets unavailable outside a live/dry-run bot
            available = None
        sized = risk_based_stake(
            available, stop_pct, min_stake, max_stake, self.settings.risk
        )
        return sized if sized is not None else proposed_stake

    def custom_stoploss(
        self, pair: str, trade: Trade, current_time: datetime,
        current_rate: float, current_profit: float, after_fill: bool, **kwargs,
    ) -> Optional[float]:
        row = self._latest_row(pair)
        if row is None:
            return None
        return self.trade_manager.stoploss_ratio(
            trade, current_rate, current_profit, row
        )

    # ---------------------------------------------------------------- helpers

    def _latest_row(self, pair: str) -> Optional[pd.Series]:
        """Most recent analyzed candle for the pair, or None."""
        if not self.dp:
            return None
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or dataframe.empty:
            return None
        return dataframe.iloc[-1]

    def _current_spread(self, pair: str) -> Optional[float]:
        """Relative bid/ask spread from the order book (dry-run/live only)."""
        if not self.dp or self.dp.runmode.value not in ("live", "dry_run"):
            return None
        try:
            book = self.dp.orderbook(pair, 1)
            bid, ask = book["bids"][0][0], book["asks"][0][0]
        except Exception:
            return None
        if not bid or not ask or bid <= 0:
            return None
        return (ask - bid) / ((ask + bid) / 2)
