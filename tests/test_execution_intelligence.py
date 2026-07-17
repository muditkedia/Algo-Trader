"""Execution-intelligence layer: scheduler, ranking, portfolio, sizing,
continuous management, dashboard - and their integration."""

import numpy as np
import pandas as pd
import pytest

from algo.core.enums import Direction, HoldingScope
from algo.evidence.database import EvidenceDB, MEMORY
from algo.evidence.logger import EvidenceLogger
from algo.paper import dashboard
from algo.paper.engine import PaperEngine
from algo.paper.portfolio import (
    Action, PortfolioConfig, PortfolioManager, PortfolioState,
)
from algo.paper.sizing import SizingConfig, size_position
from algo.scanner.base import Opportunity
from algo.scanner.ranking import OpportunityRanker, RankingWeights
from algo.scanner.scheduler import ScanCadence, ScanScheduler
from algo.strategies.base import StrategyMeta, StrategyProfile


# ---------------------------------------------------------------- scheduler

def test_scheduler_cadence_per_timeframe():
    clock = {"t": 0.0}
    sched = ScanScheduler(["15m", "1h", "1d"],
                          ScanCadence(tf_15m=45, tf_1h=180, tf_1d=420),
                          clock=lambda: clock["t"])
    assert sched.due() == ["15m", "1d", "1h"] or set(sched.due()) == {"15m", "1h", "1d"}
    sched.mark_scanned(["15m", "1h", "1d"])
    assert sched.due() == []
    clock["t"] = 50            # only the 15m cadence elapsed
    assert sched.due() == ["15m"]
    clock["t"] = 200           # 1h now also due; 15m due again
    assert set(sched.due()) == {"15m", "1h"}
    clock["t"] = 500
    assert set(sched.due()) == {"15m", "1h", "1d"}


def test_scheduler_next_due_and_force():
    clock = {"t": 0.0}
    sched = ScanScheduler(["15m"], ScanCadence(tf_15m=45),
                          clock=lambda: clock["t"])
    sched.mark_scanned(["15m"])
    assert sched.next_due_in() == pytest.approx(45)
    clock["t"] = 30
    assert sched.next_due_in() == pytest.approx(15)
    sched.force()
    assert sched.due() == ["15m"] and sched.next_due_in() == 0.0


def test_scheduler_cadence_configurable_not_hardcoded():
    cadence = ScanCadence.from_dict({"tf_15m": 30, "tf_1d": 600, "junk": 1})
    assert cadence.tf_15m == 30 and cadence.tf_1d == 600
    assert cadence.seconds_for("5m") == cadence.default


# ------------------------------------------------------------------ ranking

def _opp(symbol="A", strategy="s1", confidence=0.5, rr=None, cost=0.001,
         liquidity=None, expected_return=None):
    return Opportunity(symbol=symbol, strategy=strategy, direction="long",
                       confidence=confidence, risk_reward=rr,
                       est_cost_pct=cost, liquidity=liquidity or {},
                       expected_return=expected_return)


def test_ranking_weights_change_the_order():
    high_conf = _opp("A", confidence=0.9, rr=1.0)
    high_rr = _opp("B", confidence=0.3, rr=3.0)

    conf_heavy = OpportunityRanker(RankingWeights(
        confidence=1.0, risk_reward=0.0, expected_return=0, hist_expectancy=0,
        hist_win_rate=0, calibration=0, liquidity=0, reliability=0, cost=0))
    assert [o.symbol for o in conf_heavy.rank([high_conf, high_rr])][0] == "A"

    rr_heavy = OpportunityRanker(RankingWeights(
        confidence=0.0, risk_reward=1.0, expected_return=0, hist_expectancy=0,
        hist_win_rate=0, calibration=0, liquidity=0, reliability=0, cost=0))
    assert [o.symbol for o in rr_heavy.rank([high_conf, high_rr])][0] == "B"


def test_ranking_uses_evidence_stats_and_neutral_without():
    db = EvidenceDB(MEMORY)
    log = EvidenceLogger(db)
    sid = log.register_strategy("s1", "1.0")
    log.record_evaluation(sid, "overall", "all", n_trades=100,
                          net_expectancy=0.008, win_rate=0.6,
                          profit_factor=1.6)
    ranker = OpportunityRanker(db=db)
    ranker.refresh_stats()

    with_evidence = ranker.score(_opp(strategy="s1"))[1]
    without = ranker.score(_opp(strategy="unknown"))[1]
    assert with_evidence["hist_expectancy"] > without["hist_expectancy"]
    assert without["hist_expectancy"] == 0.5          # neutral, not zero
    assert with_evidence["hist_win_rate"] > 0.9        # 0.6/0.65 clipped
    db.close()


def test_ranking_breakdown_and_score_bounds():
    ranker = OpportunityRanker()
    score, breakdown = ranker.score(_opp(confidence=0.7, rr=2.0,
                                         liquidity={"volume_ratio": 1.5}))
    assert 0.0 <= score <= 1.0
    assert set(breakdown) == {"expected_return", "hist_expectancy",
                              "hist_win_rate", "confidence", "calibration",
                              "liquidity", "reliability", "cost",
                              "risk_reward"}
    assert all(0.0 <= v <= 1.0 for v in breakdown.values())


# ---------------------------------------------------------------- portfolio

def _state(**kwargs):
    return PortfolioState(**kwargs)


def test_portfolio_open_when_clear():
    manager = PortfolioManager(PortfolioConfig())
    decision = manager.evaluate(_opp(), _state(), available_capital=500_000)
    assert decision.action == Action.OPEN


def test_portfolio_skip_on_capital_and_budget():
    cfg = PortfolioConfig(total_capital=1_000_000, max_capital_deployed=0.5,
                          daily_risk_budget=0.02)
    manager = PortfolioManager(cfg)
    full = manager.evaluate(_opp(), _state(capital_deployed=500_000),
                            available_capital=100_000)
    assert full.action == Action.SKIP and "cap" in full.reason
    burned = manager.evaluate(_opp(), _state(risk_spent_today=20_000),
                              available_capital=100_000)
    assert burned.action == Action.SKIP and "budget" in burned.reason


def test_portfolio_reduce_on_soft_limits():
    cfg = PortfolioConfig(total_capital=1_000_000, daily_risk_budget=0.02,
                          risk_budget_soft_pct=0.5, reduce_multiplier=0.4)
    manager = PortfolioManager(cfg)
    decision = manager.evaluate(_opp(), _state(risk_spent_today=12_000),
                                available_capital=500_000)
    assert decision.action == Action.REDUCE
    assert decision.size_multiplier == pytest.approx(0.4)


def test_portfolio_sector_cap_and_correlation_proxy():
    cfg = PortfolioConfig(max_positions_per_sector=2)
    manager = PortfolioManager(cfg, sector_fn=lambda s: "IT")
    one_open = _state(open_positions=1,
                      position_sectors={"TCS": "IT"},
                      position_scores={"TCS": 0.5})
    reduced = manager.evaluate(_opp("INFY"), one_open, 500_000)
    assert reduced.action == Action.REDUCE and "correlated" in reduced.reason
    two_open = _state(open_positions=2,
                      position_sectors={"TCS": "IT", "WIPRO": "IT"},
                      position_scores={"TCS": 0.5, "WIPRO": 0.5})
    capped = manager.evaluate(_opp("INFY"), two_open, 500_000)
    assert capped.action == Action.SKIP and "sector cap" in capped.reason


def test_portfolio_replace_only_when_clearly_better():
    cfg = PortfolioConfig(max_open_positions=2, replace_min_improvement=0.15)
    manager = PortfolioManager(cfg)
    state = _state(open_positions=2,
                   position_scores={"AAA": 0.40, "BBB": 0.70},
                   position_sectors={"AAA": None, "BBB": None})
    better = _opp("CCC")
    better.rank_score = 0.60                      # 0.60 >= 0.40 + 0.15
    decision = manager.evaluate(better, state, 500_000)
    assert decision.action == Action.REPLACE
    assert decision.replace_symbol == "AAA"       # weakest goes
    marginal = _opp("DDD")
    marginal.rank_score = 0.50                    # not better by the margin
    assert manager.evaluate(marginal, state, 500_000).action == Action.SKIP


# ------------------------------------------------------------------- sizing

CFG = SizingConfig(risk_per_trade=0.005, min_stake=5_000, max_stake=500_000,
                   max_capital_per_trade=0.5, daily_risk_budget=0.02)


def _size(confidence=1.0, stop=0.02, budget_left=20_000, capital=1_000_000,
          available=1_000_000, config=CFG):
    return size_position(capital=capital, available_capital=available,
                         stop_pct=stop, confidence=confidence,
                         risk_budget_left=budget_left, config=config)


def test_sizing_scales_with_confidence_and_stop():
    assert _size(confidence=1.0) == pytest.approx(250_000)   # 0.5% / 2% stop
    assert _size(confidence=0.0) == pytest.approx(125_000)   # low-mult 0.5
    assert _size(stop=0.04) == pytest.approx(125_000)        # wider stop, less


def test_sizing_respects_every_cap():
    assert _size(budget_left=2_000) == pytest.approx(100_000)   # budget/stop
    assert _size(available=60_000) == pytest.approx(60_000)     # capital left
    tight = SizingConfig(**{**CFG.__dict__, "max_stake": 50_000})
    assert _size(config=tight) == pytest.approx(50_000)          # hard cap
    conc = SizingConfig(**{**CFG.__dict__, "max_capital_per_trade": 0.1})
    assert _size(config=conc) == pytest.approx(100_000)          # concentration


def test_sizing_returns_zero_below_minimum_or_invalid():
    assert _size(budget_left=50) == 0.0            # honest skip, never forced
    assert _size(stop=None) == 0.0
    assert _size(available=0) == 0.0


# ---------------------------------------------------------------- dashboard

def _snapshot():
    return {"as_of": "2024-03-04 10:00:00+00:00",
            "strategies": ["orb_15m", "nr7_daily"],
            "positions": [{"symbol": "TCS", "strategy": "orb_15m",
                           "entry": 100.0, "last": 101.0, "stop": 98.0,
                           "stake": 50_000.0, "unrealized": 500.0,
                           "trailed": True}],
            "capital_total": 1_000_000.0, "capital_deployed": 50_000.0,
            "capital_available": 550_000.0, "unrealized_pnl": 500.0,
            "realized_pnl_today": -120.0, "trades_today": 2,
            "risk_budget_left": 15_000.0,
            "last_scan": {"timeframe": "15m", "requested": 50, "with_data": 48,
                          "candidates": 3, "duration_ms": 812.0},
            "top_opportunities": [{"rank": 1, "symbol": "INFY",
                                   "strategy": "orb_15m", "score": 0.61,
                                   "confidence": 0.7}]}


def test_dashboard_renders_every_required_section():
    text = dashboard.render(_snapshot(), universe_size=2408, eligible=180,
                            scheduler_state={"15m": 12.0},
                            health={"data": True, "evidence": True})
    for token in ("market:", "strategies:", "universe: 2408", "eligible: 180",
                  "last scan:", "capital", "deployed", "available",
                  "realised today", "unrealised", "OPEN POSITIONS (1)",
                  "TOP RANKED OPPORTUNITIES", "INFY", "health:", "data:OK",
                  "risk", "budget left"):
        assert token in text, f"missing {token!r} in dashboard"


def test_market_status_by_ist_clock():
    assert dashboard.market_status("2024-03-04 04:30+00:00") == "OPEN"      # 10:00 IST Mon
    assert dashboard.market_status("2024-03-04 02:00+00:00") == "PRE-OPEN"  # 07:30 IST
    assert dashboard.market_status("2024-03-04 11:00+00:00") == "POST-CLOSE"
    assert dashboard.market_status("2024-03-02 05:00+00:00").startswith("CLOSED")


# ------------------------------------------------- continuous paper behavior

class AlwaysLong(StrategyProfile):
    meta = StrategyMeta(name="paper_test", version="1.0",
                        direction=Direction.LONG,
                        holding_scope=HoldingScope.INTRADAY, timeframe="15m",
                        min_bars=3, required_columns=("close",), enabled=True)

    def entry_signal(self, df):
        return pd.Series(True, index=df.index)


def _bars(day, n, closes=None, lows=None):
    dates = pd.date_range(f"{day} 09:15", periods=n, freq="15min",
                          tz="Asia/Kolkata").tz_convert("UTC")
    closes = np.asarray(closes if closes is not None
                        else np.linspace(100, 101, n))
    return pd.DataFrame({"date": dates, "open": closes, "high": closes + 0.5,
                         "low": np.asarray(lows) if lows is not None
                         else closes - 0.5,
                         "close": closes, "volume": np.full(n, 1000.0)})


@pytest.fixture
def paper(store, tmp_path):
    db = EvidenceDB(MEMORY)
    log = EvidenceLogger(db)
    for sym in ("AAA", "BBB", "CCC"):
        log.upsert_instrument(sym)
        store.write(sym, "15m", _bars("2024-03-04", 6))
    sid = log.register_strategy("paper_test", "1.0")
    log.set_strategy_status(sid, "measured", "test PASS")
    engine = PaperEngine(store, [AlwaysLong()], log,
                         state_path=tmp_path / "p.json")
    yield store, db, engine
    db.close()


def test_management_runs_even_when_no_scan_is_due(paper):
    store, db, engine = paper
    engine.cycle(as_of="2024-03-04 05:00+00:00", symbols=["AAA"])
    assert "AAA" in engine.positions
    # scheduler just scanned -> nothing due; a stop-crash must STILL close
    crash = _bars("2024-03-04", 10,
                  closes=list(np.linspace(100, 101, 6)) + [95, 94, 94, 94],
                  lows=list(np.linspace(99.5, 100.5, 6)) + [92, 92, 92, 92])
    store.write("AAA", "15m", crash)
    result = engine.cycle(as_of="2024-03-04 05:20+00:00", symbols=["AAA"])
    assert result["scanned_timeframes"] == []          # no scan happened
    assert any("AAA" in c for c in result["closed"])   # but management did


def test_portfolio_skip_recorded_to_evidence(paper):
    store, db, engine = paper
    engine.portfolio.config = PortfolioConfig(max_open_positions=2)
    engine.portfolio_cfg = engine.portfolio.config
    result = engine.cycle(as_of="2024-03-04 05:00+00:00",
                          symbols=["AAA", "BBB", "CCC"])
    assert len(result["opened"]) == 2
    assert any(d.endswith(":skip") for d in result["decisions"])
    row = db.connection.execute(
        "SELECT COUNT(*) FROM signals WHERE disposition='rejected' "
        "AND disposition_reason LIKE 'portfolio:%'").fetchone()[0]
    assert row >= 1


def test_snapshot_supports_dashboard(paper):
    store, db, engine = paper
    engine.cycle(as_of="2024-03-04 05:00+00:00", symbols=["AAA"])
    snap = engine.snapshot(as_of="2024-03-04 05:10+00:00")
    for key in ("positions", "capital_total", "capital_deployed",
                "capital_available", "unrealized_pnl", "realized_pnl_today",
                "risk_budget_left", "top_opportunities", "last_scan"):
        assert key in snap
    text = dashboard.render(snap)
    assert "OPEN POSITIONS" in text
