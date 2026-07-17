"""PaperEngine - continuous paper trading over the MarketDataStore.

The loop is TWO independent activities, so scanning never pauses because
positions exist and management never waits for a scan:

  * ``manage(as_of)``            every tick: walk new bars for each open
                                 position - initial/ratcheted stop first
                                 (conservative, same convention as the research
                                 simulator), then the intraday session
                                 square-off; closes are recorded to evidence as
                                 mode="paper" trades, net of the NSE cost model.
  * ``scan_and_open(as_of, ...)``on the adaptive scheduler's cadence: scan the
                                 due timeframes (ScanEngine + configurable
                                 OpportunityRanker), then push each ranked
                                 opportunity through the PortfolioManager
                                 (OPEN / SKIP / REDUCE / REPLACE) and dynamic
                                 risk-based sizing before a position opens.

``cycle()`` runs both (compatibility + tests). Position state persists to JSON
so restarts resume exactly; the daily risk budget resets at the IST day roll.

GATING: strategies must have survived measurement on real data (evidence
status measured/validated/approved/paper); ``allow_unmeasured`` exists for
offline pipeline validation only. NO live orders exist anywhere in this module.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from algo.core.costs import CostModel, NseEquityCostModel, Product
from algo.core.enums import Disposition, HoldingScope, Mode
from algo.core.logging import get_logger
from algo.evidence.logger import EvidenceLogger
from algo.evidence.models import TradeRecord
from algo.paper.portfolio import (
    Action, PortfolioConfig, PortfolioManager, PortfolioState,
)
from algo.paper.sizing import SizingConfig, size_position
from algo.risk.engine import RiskParams, initial_stop_pct, trailing_stop_price
from algo.scanner.engine import ScanEngine
from algo.scanner.ranking import OpportunityRanker, RankingScales, RankingWeights
from algo.scanner.scheduler import ScanCadence, ScanScheduler

logger = get_logger("paper.engine")

#: Evidence lifecycle statuses that count as "survived measurement".
TRADEABLE_STATUSES = ("measured", "validated", "approved", "paper", "live")
#: Intraday square-off time (IST) - NSE MIS convention.
SQUAREOFF_IST = "15:15"
IST = "Asia/Kolkata"


@dataclass
class PaperPosition:
    symbol: str
    strategy: str
    timeframe: str
    product: str                      # Product value
    entry_ts: str                     # ISO, bar close time of entry
    entry_price: float
    quantity: float
    stake: float
    stop_price: float
    stop_distance_pct: float
    atr_at_entry: float
    trailed: bool = False
    last_seen_ts: str = ""            # last bar processed for this position
    signal_id: Optional[int] = None
    strategy_id: Optional[int] = None
    confidence: float = 0.0
    score: float = 0.0                # ranking score at entry (replace basis)


class PaperEngine:
    def __init__(self, store, strategies, evidence_logger: EvidenceLogger,
                 *, risk_params: Optional[RiskParams] = None,
                 cost_model: Optional[CostModel] = None,
                 portfolio: Optional[PortfolioConfig] = None,
                 sizing: Optional[SizingConfig] = None,
                 ranking_weights: Optional[RankingWeights] = None,
                 ranking_scales: Optional[RankingScales] = None,
                 cadence: Optional[ScanCadence] = None,
                 max_positions: Optional[int] = None,
                 stake_per_trade: Optional[float] = None,
                 min_confidence: float = 0.0,
                 state_path="user_data/paper/positions.json",
                 allow_unmeasured: bool = False) -> None:
        self.store = store
        self.log = evidence_logger
        self.risk = risk_params or RiskParams()
        self.costs = cost_model or NseEquityCostModel()

        portfolio = portfolio or PortfolioConfig()
        if max_positions is not None:       # legacy convenience override
            portfolio = PortfolioConfig(**{**portfolio.__dict__,
                                           "max_open_positions": max_positions})
        self.portfolio_cfg = portfolio
        sizing = sizing or SizingConfig()
        if stake_per_trade is not None:     # legacy param = per-trade cap
            sizing = SizingConfig(**{**sizing.__dict__,
                                     "max_stake": stake_per_trade,
                                     "min_stake": min(sizing.min_stake,
                                                      stake_per_trade)})
        self.sizing_cfg = sizing
        self.min_confidence = min_confidence
        self.state_path = Path(state_path)

        self.strategies = self._gate(strategies, allow_unmeasured)
        self.portfolio = PortfolioManager(portfolio, sector_fn=self._sector_for)
        self.ranker = OpportunityRanker(ranking_weights, ranking_scales,
                                        db=self.log.db)
        self.scanner = ScanEngine(store, self.strategies,
                                  evidence_logger=evidence_logger,
                                  mode=Mode.PAPER.value, ranker=self.ranker,
                                  risk_params=self.risk, cost_model=self.costs,
                                  regime_fn=self._regime_for)
        self.scheduler = ScanScheduler(self.scanner.timeframes(),
                                       cadence or ScanCadence())
        self.positions: Dict[str, PaperPosition] = {}
        self._risk_day: Optional[str] = None
        self._risk_spent: float = 0.0
        self._regime_cache: Dict[str, tuple] = {}
        self.last_scan_summary: dict = {}
        self.last_cycle_ts: Optional[str] = None
        self._load_state()

    # ---------------------------------------------------------------- gating

    def _gate(self, strategies, allow_unmeasured: bool) -> list:
        admitted, refused = [], []
        for strategy in strategies:
            row = self.log.db.connection.execute(
                "SELECT status FROM strategies WHERE name = ? "
                "ORDER BY strategy_id DESC LIMIT 1",
                (strategy.name,)).fetchone()
            status = row["status"] if row else "unregistered"
            if status in TRADEABLE_STATUSES:
                admitted.append(strategy)
            else:
                refused.append((strategy.name, status))
        if refused and not allow_unmeasured:
            for name, status in refused:
                logger.warning("REFUSED %s (status=%s): has not survived "
                               "measurement on real data", name, status)
        elif refused and allow_unmeasured:
            logger.warning("allow_unmeasured=True: admitting %s for OFFLINE "
                           "pipeline validation only - not a trading verdict",
                           [n for n, _ in refused])
            admitted += [s for s in strategies
                         if s.name in {n for n, _ in refused}]
        if not admitted:
            raise RuntimeError(
                "no strategy has survived measurement (status in "
                f"{TRADEABLE_STATUSES}). Run the measurement on real data "
                "first, or pass allow_unmeasured=True for offline validation.")
        return admitted

    # ------------------------------------------------------------- context fns

    def _sector_for(self, symbol: str) -> Optional[str]:
        row = self.log.db.connection.execute(
            "SELECT sector FROM instruments WHERE symbol = ?",
            (symbol,)).fetchone()
        return row["sector"] if row and row["sector"] else None

    def _regime_for(self, symbol: str) -> Optional[str]:
        """Daily trend regime via the validation labeler (cached per day)."""
        today = str(pd.Timestamp.now(tz="UTC").date())
        cached = self._regime_cache.get(symbol)
        if cached and cached[0] == today:
            return cached[1]
        try:
            daily = self.store.read(symbol, "1d")
            if len(daily) < 60:
                label = None
            else:
                from algo.research.validation.regime import label_daily
                label = str(label_daily(daily.tail(400))
                            ["trend_label"].iloc[-1])
        except Exception:
            label = None
        self._regime_cache[symbol] = (today, label)
        return label

    # ----------------------------------------------------------------- state

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        positions = payload.get("positions", payload)   # legacy flat format
        self.positions = {key: PaperPosition(**value)
                          for key, value in positions.items()}
        self._risk_day = payload.get("risk_day")
        self._risk_spent = float(payload.get("risk_spent", 0.0))
        logger.info("resumed %d open paper position(s)", len(self.positions))

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({
            "positions": {key: asdict(pos)
                          for key, pos in self.positions.items()},
            "risk_day": self._risk_day,
            "risk_spent": self._risk_spent,
        }, indent=1), encoding="utf-8")

    # ------------------------------------------------------------ risk budget

    def _roll_risk_day(self, as_of: pd.Timestamp) -> None:
        day = str(as_of.tz_convert(IST).date())
        if self._risk_day != day:
            self._risk_day = day
            self._risk_spent = 0.0

    def _risk_budget_left(self) -> float:
        budget = (self.portfolio_cfg.total_capital
                  * self.portfolio_cfg.daily_risk_budget)
        return max(0.0, budget - self._risk_spent)

    # -------------------------------------------------------- portfolio state

    def capital_deployed(self) -> float:
        return sum(p.stake for p in self.positions.values())

    def available_capital(self) -> float:
        return max(0.0, self.portfolio_cfg.total_capital
                   * self.portfolio_cfg.max_capital_deployed
                   - self.capital_deployed())

    def _portfolio_state(self) -> PortfolioState:
        return PortfolioState(
            open_positions=len(self.positions),
            capital_deployed=self.capital_deployed(),
            position_scores={s: p.score for s, p in self.positions.items()},
            position_sectors={s: self._sector_for(s)
                              for s in self.positions},
            risk_spent_today=self._risk_spent)

    # ----------------------------------------------------------------- cycle

    def cycle(self, as_of=None, symbols: Optional[List[str]] = None) -> dict:
        """One full pass: manage positions, then scan due timeframes + open."""
        as_of = pd.Timestamp(as_of) if as_of is not None \
            else pd.Timestamp.now(tz="UTC")
        self._roll_risk_day(as_of)
        closed = self.manage(as_of)
        due = self.scheduler.due()
        opened, decisions = [], []
        scan_summary = {}
        if due:
            opened, decisions, scan_summary = self.scan_and_open(
                as_of, symbols or [], timeframes=due)
            self.scheduler.mark_scanned(due)
        self._save_state()
        self.last_cycle_ts = str(as_of)
        summary = {"as_of": str(as_of), "scanned_timeframes": due,
                   "opened": [p.symbol for p in opened], "closed": closed,
                   "decisions": decisions,
                   "open_positions": len(self.positions),
                   "scan": scan_summary}
        logger.info("paper cycle: %s", summary)
        return summary

    # ---------------------------------------------------------- scan + open

    def scan_and_open(self, as_of, symbols: List[str],
                      timeframes: Optional[List[str]] = None) -> tuple:
        scan = self.scanner.scan(as_of, symbols, timeframes=timeframes)
        self.last_scan_summary = scan.summary()
        self.last_opportunities = scan.opportunities
        opened, decisions = [], []
        for opp in scan.opportunities:
            if opp.confidence < self.min_confidence:
                continue
            if opp.symbol in self.positions:
                continue
            decision = self.portfolio.evaluate(opp, self._portfolio_state(),
                                               self.available_capital())
            decisions.append(f"{opp.symbol}:{decision.action.value}")
            if decision.action == Action.SKIP:
                self._mark_skip(opp, decision.reason)
                continue
            if decision.action == Action.REPLACE and decision.replace_symbol:
                self._close_at_market(decision.replace_symbol, as_of,
                                      "replaced_by_better_opportunity")
            position = self._open(opp, as_of, decision.size_multiplier)
            if position is not None:
                opened.append(position)
        return opened, decisions, self.last_scan_summary

    def _mark_skip(self, opp, reason: str) -> None:
        if opp.signal_id is not None:
            try:
                self.log.update_disposition(opp.signal_id,
                                            Disposition.REJECTED.value,
                                            f"portfolio: {reason}")
            except Exception:   # evidence must never break trading
                pass

    # ------------------------------------------------------------------ open

    def _open(self, opp, as_of, size_multiplier: float = 1.0
              ) -> Optional[PaperPosition]:
        stop_pct = opp.expected_risk
        entry_price = opp.entry_price
        if not entry_price:
            return None
        if not stop_pct:
            # Strategy exposes no ATR - fall back to a structure/1% stop (the
            # pre-extension behavior) so risk is still defined, never absent.
            bars = self.store.read(opp.symbol, opp.timeframe or "1d",
                                   end=as_of)
            if bars.empty:
                return None
            swing_low = float(bars["low"].tail(10).min())
            stop_pct = initial_stop_pct(entry_price, entry_price * 0.01,
                                        swing_low, self.risk)
            if stop_pct is None:
                return None
            stop_pct = min(stop_pct, self.risk.hard_stop_pct)
        stake = size_position(
            capital=self.portfolio_cfg.total_capital,
            available_capital=self.available_capital(),
            stop_pct=stop_pct, confidence=opp.confidence,
            risk_budget_left=self._risk_budget_left(),
            config=self.sizing_cfg) * size_multiplier
        if stake < self.sizing_cfg.min_stake:
            self._mark_skip(opp, "sized below minimum stake")
            return None

        if opp.signal_id is not None:
            try:
                self.log.update_disposition(opp.signal_id,
                                            Disposition.EXECUTED.value,
                                            "paper position opened")
            except Exception:
                pass
        strategy_id = self.log.register_strategy(
            opp.strategy,
            next(s.meta.version for s in self.strategies
                 if s.name == opp.strategy))
        atr = ((opp.expected_risk / self.risk.atr_stop_multiplier)
               * entry_price if opp.expected_risk else entry_price * 0.01)
        position = PaperPosition(
            symbol=opp.symbol, strategy=opp.strategy,
            timeframe=opp.timeframe or "1d",
            product=(Product.INTRADAY.value
                     if next(s.meta.holding_scope for s in self.strategies
                             if s.name == opp.strategy) == HoldingScope.INTRADAY
                     else Product.DELIVERY.value),
            entry_ts=opp.signal_ts or str(as_of),
            entry_price=entry_price,
            quantity=stake / entry_price, stake=stake,
            stop_price=opp.stop_price or entry_price * (1 - stop_pct),
            stop_distance_pct=stop_pct, atr_at_entry=atr,
            last_seen_ts=opp.signal_ts or str(as_of),
            signal_id=opp.signal_id, strategy_id=strategy_id,
            confidence=opp.confidence,
            score=opp.rank_score if opp.rank_score is not None
            else opp.confidence)
        self.positions[opp.symbol] = position
        self._risk_spent += stake * stop_pct
        logger.info("OPEN paper %s %s @ %.2f stake %.0f stop %.2f "
                    "(conf %.2f score %.2f)", position.strategy,
                    position.symbol, entry_price, stake,
                    position.stop_price, opp.confidence, position.score)
        return position

    # ---------------------------------------------------------------- manage

    def manage(self, as_of) -> List[str]:
        """Walk new bars for every open position; close on stop/square-off."""
        closed = []
        for symbol in list(self.positions):
            position = self.positions[symbol]
            bars = self.store.read(symbol, position.timeframe, end=as_of)
            new = bars[bars["date"] > pd.Timestamp(position.last_seen_ts)]
            for _, bar in new.iterrows():
                exit_price, reason = self._check_exit(position, bar)
                if exit_price is not None:
                    self._close(position, exit_price, reason,
                                pd.Timestamp(bar["date"]))
                    closed.append(f"{symbol}:{reason}")
                    break
                position.last_seen_ts = str(pd.Timestamp(bar["date"]))
        return closed

    def _check_exit(self, position: PaperPosition, bar) -> tuple:
        low, close = float(bar["low"]), float(bar["close"])
        bar_ts = pd.Timestamp(bar["date"])
        # 1) stop first (conservative, matches the research simulator)
        if low <= position.stop_price:
            return position.stop_price, ("trailing_stop" if position.trailed
                                         else "stop_loss")
        # 2) intraday square-off at/after the IST cutoff (market rule)
        if position.product == Product.INTRADAY.value:
            ist = bar_ts.tz_convert(IST)
            cutoff = pd.Timestamp(f"{ist.date()} {SQUAREOFF_IST}", tz=IST)
            entry_day = pd.Timestamp(position.entry_ts).tz_convert(IST).date()
            if ist >= cutoff or ist.date() > entry_day:
                return close, "session_squareoff"
        # 3) ratchet the stop (never widened)
        profit = close / position.entry_price - 1.0
        candidate = trailing_stop_price(position.entry_price, close, profit,
                                        position.atr_at_entry, self.risk)
        if candidate is not None:
            raised = min(candidate, close * (1 - 1e-4))
            if raised > position.stop_price:
                position.stop_price, position.trailed = raised, True
        return None, ""

    def _close_at_market(self, symbol: str, as_of, reason: str) -> None:
        position = self.positions.get(symbol)
        if position is None:
            return
        bars = self.store.read(symbol, position.timeframe, end=as_of)
        if bars.empty:
            return
        last = bars.iloc[-1]
        self._close(position, float(last["close"]), reason,
                    pd.Timestamp(last["date"]))

    def _close(self, position: PaperPosition, exit_price: float, reason: str,
               ts: pd.Timestamp) -> None:
        gross = exit_price / position.entry_price - 1.0
        cost = self.costs.round_trip_pct(
            entry_price=position.entry_price, exit_price=exit_price,
            quantity=position.quantity, product=Product(position.product))
        net = gross - cost
        duration = (ts - pd.Timestamp(position.entry_ts)).total_seconds() / 60.0
        self.log.record_trade(TradeRecord(
            mode=Mode.PAPER.value, symbol=position.symbol,
            open_date=position.entry_ts, close_date=str(ts),
            profit_ratio=net, profit_abs=net * position.stake,
            stake_amount=position.stake, trade_duration=duration,
            exit_reason=reason, enter_tag=position.strategy,
            stop_distance_pct=position.stop_distance_pct,
            signal_id=position.signal_id, strategy_id=position.strategy_id))
        del self.positions[position.symbol]
        logger.info("CLOSE paper %s %s @ %.2f (%s) net %+.4f",
                    position.strategy, position.symbol, exit_price, reason, net)

    # -------------------------------------------------------------- snapshot

    def snapshot(self, as_of=None) -> dict:
        """Status snapshot for the dashboard (monitoring only)."""
        as_of = pd.Timestamp(as_of) if as_of is not None \
            else pd.Timestamp.now(tz="UTC")
        positions = []
        unrealized = 0.0
        for position in self.positions.values():
            bars = self.store.read(position.symbol, position.timeframe,
                                   end=as_of)
            last_close = (float(bars["close"].iloc[-1]) if not bars.empty
                          else position.entry_price)
            pnl = (last_close / position.entry_price - 1.0) * position.stake
            unrealized += pnl
            positions.append({
                "symbol": position.symbol, "strategy": position.strategy,
                "entry": position.entry_price, "last": last_close,
                "stop": position.stop_price, "stake": position.stake,
                "unrealized": pnl, "trailed": position.trailed})
        day_ist = str(as_of.tz_convert(IST).date())
        row = self.log.db.connection.execute(
            "SELECT COALESCE(SUM(profit_abs), 0) AS pnl, COUNT(*) AS n "
            "FROM trades WHERE mode = 'paper' AND close_date LIKE ?",
            (f"{day_ist}%",)).fetchone()
        return {
            "as_of": str(as_of),
            "strategies": [s.name for s in self.strategies],
            "timeframes": self.scanner.timeframes(),
            "positions": positions,
            "capital_total": self.portfolio_cfg.total_capital,
            "capital_deployed": self.capital_deployed(),
            "capital_available": self.available_capital(),
            "unrealized_pnl": unrealized,
            "realized_pnl_today": float(row["pnl"]),
            "trades_today": int(row["n"]),
            "risk_budget_left": self._risk_budget_left(),
            "last_scan": self.last_scan_summary,
            "last_cycle": self.last_cycle_ts,
            "top_opportunities": [
                {"rank": o.rank, "symbol": o.symbol, "strategy": o.strategy,
                 "score": o.rank_score, "confidence": o.confidence}
                for o in getattr(self, "last_opportunities", [])[:5]],
        }
