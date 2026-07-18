"""Research Engine - turns accumulated evidence into strategy verdicts.

This is the heart of the platform. It reads bars/signals, drives them through
the reused validation battery (metrics, Monte Carlo, walk-forward, sensitivity,
stress, portfolio, regime, report), and writes per-scope ``evaluations`` that
the confidence layer later reads.

Phase 5 completes the loop:

  * ``prepare_signals``   - indicators + entry signal, computed ONCE per symbol
  * ``record_signals``    - persist every historical candidate with confidence
  * ``measure_edge``      - the D-007 gate: does the ENTRY, exits ignored, beat
                            the cost hurdle? (promoted L-009 methodology)
  * ``label_outcomes``    - attach realized outcomes to recorded signals
  * ``simulate_strategy`` - replay managed trades into the canonical schema
  * ``evaluate_strategy`` - the full metric battery + cost sensitivity +
                            confidence calibration, persisted as an evaluation
  * ``league_table``      - rank every strategy and issue PASS / BORDERLINE /
                            FAIL verdicts against the PRE-REGISTERED bars

Phase 8 closes it into one seam: ``research`` runs that whole sequence for a
single strategy and ``research_all`` sweeps a library, so implementing a
candidate is the only work adding one requires - measurement, evidence, the
league table and the promotion decision follow with no further wiring.

The verdict thresholds are NOT new: they are the project's own pre-registered
decisions (D-007's "edge must exceed ~2x round-trip cost", and the frozen
VALIDATION_RULES §7 profit-factor / expectancy / drawdown bars). Nothing here
is tuned to make a strategy pass, and nothing here may be re-picked after
seeing a result - including the measurement horizon, which each strategy
pre-registers in its own ``meta`` (see algo.strategies.base).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

import numpy as np
import pandas as pd

from algo.core.config import MarketConfig
from algo.core.costs import CostModel, NseEquityCostModel, Product
from algo.core.indicators import atr as atr_series
from algo.core.logging import get_logger
from algo.data.ohlcv import timeframe_minutes
from algo.evidence.database import EvidenceDB
from algo.evidence.logger import EvidenceLogger
from algo.evidence.models import Disposition, Mode, Signal
from algo.research import edge_lab
from algo.research.labeler import OutcomeLabeler
from algo.research.simulator import simulate_trade
from algo.research.validation import coordinator, loaders, metrics
from algo.risk.engine import RiskParams

logger = get_logger("research.engine")

#: Columns the canonical trades frame needs (validation package contract).
_TRADE_COLUMNS = (
    "pair", "open_date", "close_date", "profit_ratio", "profit_abs",
    "stake_amount", "trade_duration", "exit_reason", "enter_tag",
    "stop_distance_pct",
)

# --- Pre-registered acceptance bars (NOT invented here) --------------------
#: D-007: an entry must clear this multiple of the round-trip cost to be worth
#: implementing. The crypto entry was "significant" and still died at ~0.15x.
EDGE_COST_MULTIPLE = 2.0
#: VALIDATION_RULES SS7 point gates (equity-applicable subset).
MIN_PROFIT_FACTOR = 1.25
BORDERLINE_PROFIT_FACTOR = 1.0
#: SS8: below this many trades a bucket is "insufficient evidence" = inconclusive.
MIN_TRADES = 30
#: D-031 (the benchmark amendment): the binding promotion gate is now the
#: SELECTION edge - forward return in excess of a random entry from the same
#: corpus - not the absolute return. Absolute return can be won by market
#: participation alone (L-010: 2 of 3 random-entry controls PASSed the old
#: bars at multi-week horizons). The selection edge's block-bootstrap CI lower
#: bound must clear the round-trip cost, exactly as D-007 required of the
#: absolute edge. The absolute D-007 checks are RETAINED (a strategy must both
#: beat random AND clear cost in absolute terms); the selection gate is added.


def product_for_strategy(strategy) -> Product:
    """The NSE product a strategy's declared holding scope implies.

    Intraday is squared off in-session (MIS); anything else settles (CNC). The
    distinction is first-order, not cosmetic: delivery pays STT on BOTH sides
    (~31 bps round trip) where intraday pays it once (~12 bps).
    """
    return (Product.INTRADAY
            if strategy.meta.holding_scope.value == "intraday"
            else Product.DELIVERY)


@dataclass(frozen=True)
class SignalSet:
    """One strategy's prepared frames + entry signals, keyed by symbol.

    Holds only symbols that actually fired. Recording, edge measurement and
    simulation each used to call ``prepare()`` + ``entry_signal()`` themselves,
    so the indicator work ran three times per strategy per symbol; computing it
    once and threading it through removes two of those passes.

    Measured, so the claim is not oversold: on the real corpus this is ~1.5s per
    pass for a 15m strategy over 2.16M bars, so the saving is ~3s per strategy -
    real, but the indicator work was never the bottleneck. Vectorized pandas is
    simply fast. The costs that dominate a sweep are the per-signal ones
    (recording, labeling, simulation), which is where the throughput work
    actually had to go.
    """

    frames: Dict[str, pd.DataFrame] = field(default_factory=dict)
    signals: Dict[str, pd.Series] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.frames)

    def n_signals(self) -> int:
        return int(sum(int(s.to_numpy(bool).sum())
                       for s in self.signals.values()))


@dataclass
class StrategyVerdict:
    strategy: str
    verdict: str                       # PASS | BORDERLINE | FAIL | INCONCLUSIVE
    reasons: List[str] = field(default_factory=list)
    edge: Optional[dict] = None
    trade_metrics: Optional[dict] = None
    cost_sensitivity: Optional[dict] = None
    calibration: Optional[dict] = None
    #: Benchmark battery (D-031): comparative metrics vs buy&hold and random
    #: entry. None when benchmarks were not run (the cheap selection gate in
    #: the ``edge`` dict is always present; the managed battery is opt-in).
    benchmarks: Optional[dict] = None
    #: Evidence written by this run (``research`` only; ``measure_all`` neither
    #: records nor labels, and leaves these None rather than reporting a zero
    #: that would read as "nothing fired").
    n_recorded: Optional[int] = None
    n_labeled: Optional[int] = None

    def _best_selection_horizon(self) -> dict:
        """The horizon the amended gate turns on: max selection edge."""
        horizons = (self.edge or {}).get("horizons") or []
        scored = [h for h in horizons if h.get("selection_bps") is not None]
        return max(scored, key=lambda h: h["selection_bps"]) if scored else {}

    def as_row(self) -> dict:
        m = self.trade_metrics or {}
        horizons = (self.edge or {}).get("horizons") or []
        best = max(horizons, key=lambda h: h.get("gross_mean_bps") or -1e9) \
            if horizons else {}
        sel = self._best_selection_horizon()
        bench = self.benchmarks or {}
        return {
            "strategy": self.strategy, "verdict": self.verdict,
            "n_signals": (self.edge or {}).get("n_signals", 0),
            "trades": m.get("trades", 0),
            "expectancy": m.get("expectancy"),
            "win_rate": m.get("win_rate"),
            "profit_factor": m.get("profit_factor"),
            "sharpe": m.get("sharpe"), "sortino": m.get("sortino"),
            "max_drawdown_pct": m.get("max_drawdown_pct"),
            "median_hold_min": (self.trade_metrics or {}).get("median_hold_min"),
            "edge_bps": best.get("gross_mean_bps"),
            "edge_ci_low_bps": (best.get("ci95_bps") or [None])[0],
            "selection_bps": sel.get("selection_bps"),
            "selection_ci_low_bps": (sel.get("selection_ci_bps") or [None])[0],
            "cost_bps": round((self.edge or {}).get("cost_pct", 0) * 1e4, 2),
            "excess_vs_bh": bench.get("excess_vs_bh"),
            "excess_vs_random": bench.get("excess_vs_random"),
            "rel_pf": bench.get("relative_profit_factor"),
            "mfe_mae": (self.edge or {}).get("mfe_mae_ratio"),
            "conf_corr": (self.calibration or {}).get("correlation"),
            "reasons": "; ".join(self.reasons),
        }


class ResearchEngine:
    """Orchestrates evidence + the validation battery into strategy evidence."""

    def __init__(self, db: EvidenceDB, *, market: Optional[MarketConfig] = None,
                 cost_model: Optional[CostModel] = None,
                 store=None, risk_params: Optional[RiskParams] = None) -> None:
        self.db = db
        self.logger_ = EvidenceLogger(db)
        self.market = market or MarketConfig()
        self.cost_model = cost_model or NseEquityCostModel()
        self.store = store
        self.risk_params = risk_params or RiskParams()

    # ------------------------------------------------------- evidence access

    def trades_frame(self, strategy_id: Optional[int] = None,
                     mode: Optional[str] = None) -> pd.DataFrame:
        """Load stored trades into the canonical validation schema.

        The evidence ``trades`` table uses ``symbol``; the validation package
        expects ``pair``. We alias at this single boundary so the whole battery
        is reused unchanged.
        """
        clauses, params = [], []
        if strategy_id is not None:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        if mode is not None:
            clauses.append("mode = ?")
            params.append(mode)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.connection.execute(
            "SELECT symbol AS pair, open_date, close_date, profit_ratio, "
            "profit_abs, stake_amount, trade_duration, exit_reason, enter_tag, "
            f"stop_distance_pct FROM trades{where}", params
        ).fetchall()
        frame = pd.DataFrame([dict(r) for r in rows],
                             columns=list(_TRADE_COLUMNS))
        if frame.empty:
            return frame
        if frame["trade_duration"].isna().all():
            frame = frame.drop(columns=["trade_duration"])
        return loaders.ensure_trades(frame)

    # --------------------------------------------------------- validation run

    def validate(self, trades_source: Union[str, Path, pd.DataFrame],
                 out_report: Union[str, Path],
                 options: Optional[coordinator.ValidationOptions] = None) -> dict:
        """Run the full data-free validation pipeline; returns all results."""
        return coordinator.run_validation(trades_source, out_report, options)

    # -------------------------------------------------- 0) prepare (once)

    def prepare_signals(self, strategy, symbols: Sequence[str]) -> SignalSet:
        """Indicators + entry signal for every symbol that fires, computed ONCE.

        Symbols with too little history for a warmed-up indicator set, or with
        no signal at all, are dropped - carrying them further would only make
        the downstream steps re-discover that there is nothing to measure.
        """
        if self.store is None:
            raise RuntimeError("prepare_signals needs a MarketDataStore")
        tf = strategy.meta.timeframe

        if getattr(strategy, "cross_sectional", False):
            # Two-phase: EVERY symbol's per-symbol prepare must complete before
            # the cross-sectional step can rank them, and only then can the
            # signal (which reads the injected rank columns) be computed.
            prepared_all = {}
            for symbol in symbols:
                bars = self.store.read(symbol, tf)
                if bars.empty or len(bars) < strategy.min_history():
                    continue
                prepared_all[symbol] = strategy.prepare(bars)
            prepared_all = strategy.prepare_cross_section(prepared_all)
            frames, signals = {}, {}
            for symbol, prepared in prepared_all.items():
                signal = strategy.entry_signal(prepared)
                if signal is None or not bool(signal.any()):
                    continue
                frames[symbol] = prepared
                signals[symbol] = signal
            return SignalSet(frames, signals)

        # Per-symbol path (all of batch 1) - unchanged, bit-for-bit.
        frames, signals = {}, {}
        for symbol in symbols:
            bars = self.store.read(symbol, tf)
            if bars.empty or len(bars) < strategy.min_history():
                continue
            prepared = strategy.prepare(bars)
            signal = strategy.entry_signal(prepared)
            if signal is None or not bool(signal.any()):
                continue
            frames[symbol] = prepared
            signals[symbol] = signal
        return SignalSet(frames, signals)

    # -------------------------------------------------- 1) record the evidence

    def record_signals(self, strategy, symbols: Sequence[str],
                       strategy_id: Optional[int] = None,
                       mode: str = Mode.BACKTEST.value,
                       disposition_reason: str = "measurement replay",
                       prepared: Optional[SignalSet] = None,
                       batch: int = 2000) -> int:
        """Record every historical firing bar as a signal WITH its confidence.

        Indicators are causal, so the confidence computed on the frame truncated
        at bar i is exactly what the scanner would have produced live at bar i.

        Idempotent: an already-recorded (strategy, symbol, ts, mode) is skipped,
        so re-running the pipeline never duplicates evidence.

        Reuses the logger's existing batch writer and checks the duplicate
        guard with one query, because a replay records tens of thousands of
        candidates per strategy: a transaction (a disk sync) plus a SELECT per
        signal is what made recording ~85% of the cost of a real 15m sweep.
        This is D-025's labeling finding applied to the recording step it left
        per-row.
        """
        if strategy_id is None:
            strategy_id = self.logger_.register_strategy(
                strategy.name, strategy.meta.version)
        prepared = (prepared if prepared is not None
                    else self.prepare_signals(strategy, symbols))
        seen = self.logger_.recorded_signal_keys(strategy_id, mode)
        pending: List[Signal] = []
        recorded = 0
        for symbol, frame in prepared.frames.items():
            fired = prepared.signals[symbol].to_numpy(bool)
            dates, closes = frame["date"], frame["close"].to_numpy(float)
            for i in np.flatnonzero(fired):
                ts = str(pd.Timestamp(dates.iloc[i]))
                if (symbol, ts) in seen:
                    continue
                seen.add((symbol, ts))
                conf = strategy.confidence(frame.iloc[:int(i) + 1])
                pending.append(Signal(
                    ts=ts, symbol=symbol, strategy_id=strategy_id,
                    direction=strategy.meta.direction.value, mode=mode,
                    disposition=Disposition.RECORDED_ONLY.value,
                    disposition_reason=disposition_reason,
                    entry_price=float(closes[i]),
                    confidence_score=conf.score,
                    confidence_components=conf.components or None))
                if len(pending) >= batch:
                    recorded += len(self.logger_.record_signals(pending))
                    pending = []
        return recorded + len(self.logger_.record_signals(pending))

    # ------------------------------------------------------- 2) measure_edge

    def measure_edge(self, strategy, symbols: Sequence[str],
                     product: Product = Product.INTRADAY,
                     horizon_bars: Optional[Sequence[int]] = None,
                     reference_price: float = 1000.0,
                     prepared: Optional[SignalSet] = None
                     ) -> edge_lab.EdgeReport:
        """The D-007 gate: measure the ENTRY's forward edge against costs.

        Exits are ignored entirely - this asks only whether the entry itself is
        worth more than it costs to express. Uses the promoted L-009
        methodology (day-clustered CIs, random baseline).

        Horizons come from the strategy's PRE-REGISTERED ``meta.horizon_bars``.
        ``horizon_bars`` overrides them, which exists for controls and
        diagnostics - re-running a candidate at a horizon chosen after seeing
        its verdict is the tuning D-026 forbids, so no entry point exposes it.

        CIs use the D-028 block bootstrap: the resampling block per horizon is
        the number of trading days the forward window spans, derived from the
        market's session length. Horizons inside one day keep the original
        L-009 day resample bit-for-bit.
        """
        prepared = (prepared if prepared is not None
                    else self.prepare_signals(strategy, symbols))
        horizons = tuple(horizon_bars if horizon_bars is not None
                         else strategy.meta.horizon_bars)
        qty = max(100_000.0 / reference_price, 1e-9)
        cost = self.cost_model.round_trip_pct(
            entry_price=reference_price, exit_price=reference_price,
            quantity=qty, product=product)
        tf_min = timeframe_minutes(strategy.meta.timeframe)
        # Bars per trading session: 25 for 15m, 6.25 for 1h; >= session-length
        # timeframes (1d) are one bar per day.
        bars_per_day = max(1.0, self.market.minutes_per_session / tf_min)
        return edge_lab.measure(
            prepared.frames, prepared.signals, strategy=strategy.name,
            cost_pct=cost, timeframe_minutes=tf_min, horizon_bars=horizons,
            bars_per_day=bars_per_day)

    # ----------------------------------------------------- 2) label_outcomes

    def label_outcomes(self, strategy_id: int, timeframe: str,
                       product: Product = Product.INTRADAY,
                       max_hold_bars: int = 8) -> int:
        """Fill signal_outcomes for every matured signal of a strategy."""
        if self.store is None:
            raise RuntimeError("label_outcomes needs a MarketDataStore")
        labeler = OutcomeLabeler(self.store, self.logger_, self.cost_model,
                                 risk_params=self.risk_params)
        return labeler.label_strategy(strategy_id, timeframe, product=product,
                                      max_hold_bars=max_hold_bars)

    # -------------------------------------------------- 3) simulate_strategy

    def simulate_strategy(self, strategy, symbols: Sequence[str],
                          strategy_id: Optional[int] = None,
                          product: Product = Product.INTRADAY,
                          max_hold_bars: Optional[int] = None,
                          persist: bool = True, atr_period: int = 14,
                          swing_window: int = 10,
                          prepared: Optional[SignalSet] = None) -> pd.DataFrame:
        """Replay every historical signal as a managed trade -> canonical frame.

        The hold cap comes from the strategy's pre-registered
        ``meta.max_hold_bars`` unless explicitly overridden.
        """
        prepared = (prepared if prepared is not None
                    else self.prepare_signals(strategy, symbols))
        max_bars = int(max_hold_bars if max_hold_bars is not None
                       else strategy.meta.max_hold_bars)
        entries = {sym: np.flatnonzero(sig.to_numpy(bool))
                   for sym, sig in prepared.signals.items()}
        return self.simulate_entries(
            entries, prepared, product=product, max_bars=max_bars,
            atr_period=atr_period, swing_window=swing_window,
            enter_tag=strategy.name,
            strategy_id=strategy_id if persist else None)

    def simulate_entries(self, entries_by_symbol, prepared: "SignalSet", *,
                         product: Product, max_bars: int, atr_period: int = 14,
                         swing_window: int = 10, enter_tag: str = "",
                         strategy_id: Optional[int] = None) -> pd.DataFrame:
        """Simulate a managed trade at each given entry index -> canonical frame.

        The one simulation path, shared by ``simulate_strategy`` (entries from
        the strategy's signals) and the benchmark battery (random entries), so
        a benchmark is priced through EXACTLY the same risk engine, costs and
        intrabar convention as the strategy it is compared against - the
        comparison is otherwise meaningless. ``entries_by_symbol`` maps a symbol
        to an iterable of integer bar positions into its prepared frame.
        """
        records = []
        for symbol, frame in prepared.frames.items():
            idx = entries_by_symbol.get(symbol)
            if idx is None or len(idx) == 0:
                continue
            work = frame.copy()
            work["symbol"] = symbol
            if "atr" not in work.columns:
                work["atr"] = atr_series(work, atr_period)
            work["swing_low_calc"] = work["low"].rolling(
                swing_window, min_periods=1).min()
            for i in idx:
                i = int(i)
                trade = simulate_trade(
                    work, i, atr=float(work["atr"].iloc[i]),
                    swing_low=float(work["swing_low_calc"].iloc[i]),
                    params=self.risk_params, cost_model=self.cost_model,
                    product=product, max_bars=max_bars)
                if trade is None:
                    continue
                records.append(trade)
                if strategy_id is not None:
                    self.logger_.record_trade(trade.to_record(
                        strategy_id=strategy_id, enter_tag=enter_tag))
        if not records:
            return pd.DataFrame(columns=list(_TRADE_COLUMNS))
        frame = pd.DataFrame([{
            "pair": t.symbol, "open_date": t.open_date,
            "close_date": t.close_date, "profit_ratio": t.profit_ratio,
            "profit_abs": t.profit_abs, "stake_amount": t.stake_amount,
            "trade_duration": t.holding_min, "exit_reason": t.exit_reason,
            "enter_tag": enter_tag, "stop_distance_pct": t.stop_distance_pct,
            "gross_ratio": t.gross_ratio, "mfe_pct": t.mfe_pct,
            "mae_pct": t.mae_pct,
        } for t in records])
        return loaders.ensure_trades(frame)

    # --------------------------------------------------- 4) evaluate_strategy

    def evaluate_strategy(self, strategy_id: int,
                          start_capital: float = 100_000.0,
                          mode: Optional[str] = None,
                          run_id: Optional[int] = None,
                          trades: Optional[pd.DataFrame] = None) -> dict:
        """Full metric battery for a strategy; persists an overall evaluation."""
        frame = trades if trades is not None else self.trades_frame(
            strategy_id, mode=mode)
        if frame.empty:
            logger.info("strategy %s has no trades to evaluate", strategy_id)
            self.logger_.record_evaluation(
                strategy_id, "overall", "all", n_trades=0, run_id=run_id,
                verdict="no_trades")
            return {}
        summary = metrics.summarize(frame, start_capital)
        holding = metrics.holding_time_stats(frame, by="exit_reason")
        summary["median_hold_min"] = holding.get("median")
        summary["holding"] = holding
        summary["exit_reasons"] = {
            str(k): int(v) for k, v in
            frame["exit_reason"].value_counts().to_dict().items()}
        for column in ("mfe_pct", "mae_pct", "gross_ratio"):
            if column in frame.columns:
                summary[f"mean_{column}"] = float(frame[column].mean())
        self.logger_.record_evaluation(
            strategy_id, "overall", "all",
            n_trades=summary.get("trades"),
            net_expectancy=summary.get("expectancy"),
            win_rate=summary.get("win_rate"),
            profit_factor=summary.get("profit_factor"),
            run_id=run_id)
        logger.info("evaluated strategy %s: %d trades, PF=%s, expectancy=%s",
                    strategy_id, summary.get("trades"),
                    summary.get("profit_factor"), summary.get("expectancy"))
        return summary

    # ------------------------------------------------ cost sensitivity / calib

    def cost_sensitivity(self, trades: pd.DataFrame,
                         start_capital: float = 100_000.0,
                         multiples: Sequence[float] = (1.0, 2.0)) -> dict:
        """Re-price the same trades at multiples of the modelled cost.

        A strategy whose edge evaporates at 2x cost is too thin to deploy
        (VALIDATION_RULES SS15 cost-fragility test, reused).
        """
        if trades.empty or "gross_ratio" not in trades.columns:
            return {}
        base_cost = (trades["gross_ratio"] - trades["profit_ratio"]).abs()
        out = {}
        for mult in multiples:
            stressed = trades.copy()
            stressed["profit_ratio"] = trades["gross_ratio"] - base_cost * mult
            stressed["profit_abs"] = (stressed["profit_ratio"]
                                      * stressed["stake_amount"])
            summary = metrics.summarize(stressed, start_capital)
            out[f"{mult:g}x"] = {
                "expectancy": summary.get("expectancy"),
                "profit_factor": summary.get("profit_factor"),
                "net_profit_abs": summary.get("net_profit_abs"),
            }
        return out

    def confidence_calibration(self, strategy_id: int, bins: int = 4) -> dict:
        """Do higher-confidence signals actually do better?

        Correlates the recorded confidence score against the realized simulated
        P&L. This is the FIRST evidence about whether the Phase-4 heuristic
        confidences carry any signal at all - exactly the L-003 question.
        """
        from algo.evidence import queries as evidence_queries
        rows = self.db.connection.execute(
            "SELECT s.confidence_score AS conf, o.sim_pnl_net AS pnl "
            "FROM signals s JOIN signal_outcomes o USING (signal_id) "
            "WHERE s.strategy_id = ? AND s.confidence_score IS NOT NULL "
            "AND o.sim_pnl_net IS NOT NULL", (strategy_id,)).fetchall()
        frame = pd.DataFrame([dict(r) for r in rows])
        if len(frame) < 10 or frame["conf"].nunique() < 2:
            return {"status": "insufficient evidence", "n": int(len(frame))}
        # shared calibration statistic (single implementation, evidence.queries)
        corr = evidence_queries.confidence_outcome_correlation(
            self.db, strategy_id)
        try:
            frame["bin"] = pd.qcut(frame["conf"], q=min(bins, frame["conf"].nunique()),
                                   duplicates="drop")
            table = frame.groupby("bin", observed=True)["pnl"].agg(
                ["count", "mean"])
            buckets = {str(k): {"n": int(v["count"]), "mean_pnl": float(v["mean"])}
                       for k, v in table.iterrows()}
        except ValueError:
            buckets = {}
        return {"status": "computed", "n": int(len(frame)),
                "correlation": round(corr, 4) if corr is not None else None,
                "buckets": buckets}

    # --------------------------------------------------------- 5) the verdict

    def verdict_for(self, strategy, edge: edge_lab.EdgeReport,
                    trade_metrics: dict, cost_sens: dict,
                    calibration: dict,
                    benchmarks: Optional[dict] = None) -> StrategyVerdict:
        """Apply the PRE-REGISTERED bars, amended by D-031. Hard to pass.

        The amendment adds one binding requirement to the existing bars: the
        SELECTION edge (excess over a random entry) must clear cost on its CI
        lower bound. A strategy that clears the ABSOLUTE bars but has no
        selection edge is earning market drift, not skill, and is now FAILed
        with that stated plainly (the L-010 defect, closed).
        """
        reasons: List[str] = []
        best = edge.best()
        best_sel = edge.best_selection()
        cost = edge.cost_pct

        if edge.n_signals < MIN_TRADES:
            return StrategyVerdict(
                strategy.name, "INCONCLUSIVE",
                [f"only {edge.n_signals} signals (< {MIN_TRADES} minimum "
                 "for any trusted statistic)"],
                edge.as_dict(), trade_metrics, cost_sens, calibration,
                benchmarks=benchmarks)

        # --- the D-007 gate: ABSOLUTE entry edge vs the cost hurdle ----------
        edge_ok = False
        if best is None:
            reasons.append("no measurable forward edge")
        else:
            hurdle = EDGE_COST_MULTIPLE * cost
            if best.ci_low != best.ci_low:      # NaN
                reasons.append("edge CI not computable (too few distinct days)")
            elif best.ci_low <= cost:
                reasons.append(
                    f"entry edge fails D-007: lower 95% bound "
                    f"{best.ci_low * 1e4:+.1f} bps <= round-trip cost "
                    f"{cost * 1e4:.1f} bps")
            elif best.gross_mean < hurdle:
                reasons.append(
                    f"edge {best.gross_mean * 1e4:+.1f} bps below the D-007 "
                    f"implementation bar ({hurdle * 1e4:.1f} bps = 2x cost)")
            else:
                edge_ok = True

        # --- D-031 gate: SELECTION edge vs the cost hurdle ------------------
        # The binding requirement. Absolute edge can be market drift; this
        # cannot - it is measured against a random entry from the same corpus.
        selection_ok = False
        selection_present = False
        if best_sel is None or best_sel.selection_ci_low != best_sel.selection_ci_low:
            reasons.append("selection edge not computable (no random baseline)")
        else:
            sel_lo = best_sel.selection_ci_low
            sel_pt = best_sel.selection_mean
            selection_present = sel_lo > 0
            if sel_lo <= 0:
                reasons.append(
                    f"selection edge NOT ESTABLISHED: {sel_pt * 1e4:+.1f} bps "
                    f"point but 95% lower bound {sel_lo * 1e4:+.1f} bps <= 0 - "
                    "indistinguishable from a random entry (market drift) on "
                    "this sample (D-031/L-010)")
            elif sel_lo <= cost:
                reasons.append(
                    f"selection edge {sel_pt * 1e4:+.1f} bps clears random but "
                    f"its lower bound {sel_lo * 1e4:+.1f} bps <= round-trip "
                    f"cost {cost * 1e4:.1f} bps")
            else:
                selection_ok = True

        # --- realized trade quality -----------------------------------------
        pf = trade_metrics.get("profit_factor") or 0.0
        expectancy = trade_metrics.get("expectancy") or 0.0
        n_trades = trade_metrics.get("trades") or 0
        trades_ok = False
        if n_trades < MIN_TRADES:
            reasons.append(f"only {n_trades} simulated trades (< {MIN_TRADES})")
        elif expectancy <= 0:
            reasons.append(f"negative net expectancy ({expectancy:+.5f})")
        elif pf < BORDERLINE_PROFIT_FACTOR:
            reasons.append(f"profit factor {pf:.2f} < 1.0 (loses money)")
        elif pf < MIN_PROFIT_FACTOR:
            reasons.append(f"profit factor {pf:.2f} < {MIN_PROFIT_FACTOR} "
                           "(SS7 minimum)")
        else:
            trades_ok = True

        # --- cost fragility (SS15) ------------------------------------------
        survives_2x = True
        two_x = (cost_sens or {}).get("2x") or {}
        if two_x:
            if (two_x.get("expectancy") or 0) <= 0:
                survives_2x = False
                reasons.append("net-negative under 2x costs (edge too thin)")

        # --- combine: selection skill is now REQUIRED for PASS --------------
        # The amendment only TIGHTENS: BORDERLINE still needs an absolute bar
        # (edge or trades) AND a positive selection edge, so nothing that failed
        # every absolute bar can be raised by a weak selection number, and
        # nothing with zero selection edge can pass however strong its (drift-
        # fed) absolute numbers look.
        if edge_ok and trades_ok and survives_2x and selection_ok:
            verdict = "PASS"
            reasons.insert(0, "clears the D-007 cost hurdle, the SS7 bars, AND "
                              "shows selection edge over random beyond cost "
                              "(D-031)")
        elif (edge_ok or trades_ok) and selection_present:
            # genuine on both dimensions but short of a full PASS
            verdict = "BORDERLINE"
        else:
            # no absolute bar cleared, or no selection edge at all -> FAIL
            verdict = "FAIL"
        return StrategyVerdict(strategy.name, verdict, reasons, edge.as_dict(),
                               trade_metrics, cost_sens, calibration,
                               benchmarks=benchmarks)

    def league_table(self, verdicts: Optional[List[StrategyVerdict]] = None,
                     scope_type: str = "overall") -> pd.DataFrame:
        """Rank strategies. With verdicts: the Phase-5 measurement table.
        Without: the latest stored evaluation per strategy."""
        if verdicts is not None:
            order = {"PASS": 0, "BORDERLINE": 1, "INCONCLUSIVE": 2, "FAIL": 3}
            frame = pd.DataFrame([v.as_row() for v in verdicts])
            if frame.empty:
                return frame
            frame["_o"] = frame["verdict"].map(order).fillna(9)
            frame = (frame.sort_values(
                ["_o", "profit_factor", "expectancy"],
                ascending=[True, False, False]).drop(columns="_o")
                .reset_index(drop=True))
            return frame
        rows = self.db.connection.execute(
            "SELECT e.strategy_id, s.name, s.version, s.status, e.scope_value, "
            "e.n_trades, e.net_expectancy, e.profit_factor, e.win_rate, "
            "e.ci_lo, e.wfe, e.verdict, MAX(e.as_of) AS as_of "
            "FROM evaluations e JOIN strategies s USING (strategy_id) "
            "WHERE e.scope_type = ? "
            "GROUP BY e.strategy_id, e.scope_value "
            "ORDER BY e.profit_factor DESC, e.net_expectancy DESC",
            (scope_type,)).fetchall()
        return pd.DataFrame([dict(r) for r in rows])

    # ------------------------------------------------------------ the sweep

    def _judge(self, strategy, symbols: Sequence[str], *, product: Product,
               max_hold_bars: Optional[int], start_capital: float,
               prepared: SignalSet, strategy_id: int,
               benchmarks: bool = False) -> StrategyVerdict:
        """The identical evaluation EVERY candidate is judged by.

        One implementation, so no entry point can accidentally judge a strategy
        by a different battery than the one that rejected the six on record.

        The SELECTION gate (D-031) lives inside ``edge`` and is always applied.
        The managed BENCHMARK battery (``benchmarks=True``) adds the comparative
        report (excess vs buy&hold / random, relative PF/DD, information ratio);
        it re-simulates random baselines, so it is opt-in for the real
        measurement and the re-judge, off for the fast test path.
        """
        edge = self.measure_edge(strategy, symbols, product=product,
                                 prepared=prepared)
        trades = self.simulate_strategy(
            strategy, symbols, strategy_id=strategy_id, product=product,
            max_hold_bars=max_hold_bars, persist=False, prepared=prepared)
        trade_metrics = (self.evaluate_strategy(
            strategy_id, start_capital=start_capital, trades=trades)
            if not trades.empty else {})
        cost_sens = self.cost_sensitivity(trades, start_capital)
        calibration = self.confidence_calibration(strategy_id)
        bench = None
        if benchmarks and not trades.empty:
            from algo.research import benchmarks as bench_mod
            bench = bench_mod.run_benchmarks(
                self, strategy, prepared, trades, product=product, edge=edge,
                start_capital=start_capital).summary()
        return self.verdict_for(strategy, edge, trade_metrics, cost_sens,
                                calibration, benchmarks=bench)

    def measure_all(self, strategies, symbols: Sequence[str],
                    product_for=None, max_hold_bars: Optional[int] = None,
                    start_capital: float = 100_000.0,
                    benchmarks: bool = False) -> List[StrategyVerdict]:
        """Judge every strategy from data already in the store.

        Measurement only: it neither records signals nor labels outcomes. Use
        ``research_all`` for the complete evidence loop.
        """
        verdicts = []
        for strategy in strategies:
            product = (product_for(strategy) if product_for
                       else product_for_strategy(strategy))
            strategy_id = self.logger_.register_strategy(
                strategy.name, strategy.meta.version)
            verdicts.append(self._judge(
                strategy, symbols, product=product,
                max_hold_bars=max_hold_bars, start_capital=start_capital,
                prepared=self.prepare_signals(strategy, symbols),
                strategy_id=strategy_id, benchmarks=benchmarks))
        return verdicts

    # ------------------------------------------------- the research pipeline

    def research(self, strategy, symbols: Sequence[str], *, product_for=None,
                 max_hold_bars: Optional[int] = None,
                 start_capital: float = 100_000.0, record: bool = True,
                 label: bool = True,
                 benchmarks: bool = False) -> StrategyVerdict:
        """The complete research pipeline for ONE strategy.

        register -> record every signal with its confidence -> label matured
        outcomes -> measure the entry edge -> simulate managed trades ->
        evaluate -> cost sensitivity -> confidence calibration -> verdict.

        This is the single seam a new candidate plugs into: implement the
        strategy and this runs, unchanged, against it. Indicators and the entry
        signal are computed once here and reused by every step. ``benchmarks``
        adds the D-031 managed comparison battery.
        """
        product = (product_for(strategy) if product_for
                   else product_for_strategy(strategy))
        strategy_id = self.logger_.register_strategy(strategy.name,
                                                     strategy.meta.version)
        prepared = self.prepare_signals(strategy, symbols)
        n_recorded = n_labeled = None
        if record:
            n_recorded = self.record_signals(strategy, symbols,
                                             strategy_id=strategy_id,
                                             prepared=prepared)
        if label:
            n_labeled = self.label_outcomes(
                strategy_id, strategy.meta.timeframe, product=product,
                max_hold_bars=int(max_hold_bars if max_hold_bars is not None
                                  else strategy.meta.max_hold_bars))
        verdict = self._judge(strategy, symbols, product=product,
                              max_hold_bars=max_hold_bars,
                              start_capital=start_capital, prepared=prepared,
                              strategy_id=strategy_id, benchmarks=benchmarks)
        verdict.n_recorded, verdict.n_labeled = n_recorded, n_labeled
        logger.info("researched %s: %s (%s signals recorded, %s labeled)",
                    strategy.name, verdict.verdict, n_recorded, n_labeled)
        return verdict

    def research_all(self, strategies, symbols: Sequence[str], *,
                     product_for=None, max_hold_bars: Optional[int] = None,
                     start_capital: float = 100_000.0, record: bool = True,
                     label: bool = True, benchmarks: bool = False,
                     on_verdict=None) -> List[StrategyVerdict]:
        """Run the full pipeline over a library. ``on_verdict`` is called with
        each verdict as it lands, so a long sweep can report progress without
        this module knowing anything about how it is displayed."""
        verdicts = []
        for strategy in strategies:
            verdict = self.research(
                strategy, symbols, product_for=product_for,
                max_hold_bars=max_hold_bars, start_capital=start_capital,
                record=record, label=label, benchmarks=benchmarks)
            verdicts.append(verdict)
            if on_verdict is not None:
                on_verdict(verdict)
        return verdicts
