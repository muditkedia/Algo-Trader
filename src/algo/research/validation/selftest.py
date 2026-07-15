"""Self-test for the validation package - runs WITHOUT market data.

Builds deterministic synthetic trades and daily candles, exercises every
module (loaders, metrics, portfolio, monte_carlo, sensitivity, walkforward,
regime, stress, report, coordinator, logging), and asserts protocol
invariants. This is the "run any validation possible without historical
data" step for the tooling itself.

Run inside the container:
    docker compose run --rm --entrypoint python3 freqtrade \
        user_data/scripts/validation/selftest.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # src/ (fallback if not installed)

from algo.research.validation import (  # noqa: E402
    coordinator, loaders, metrics, monte_carlo, portfolio, regime,
    sensitivity, stress, walkforward,
)
from algo.research.validation import report as report_mod  # noqa: E402
from algo.research.validation.logging_utils import configure  # noqa: E402

FAILURES: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


# ------------------------------------------------------------ synthetic data

def synthetic_trades(n: int = 500, seed: int = 7) -> pd.DataFrame:
    """Deterministic trades: 2 pairs, overlapping, slight positive edge."""
    rng = np.random.default_rng(seed)
    opens = pd.Timestamp("2024-01-02", tz="UTC") + pd.to_timedelta(
        np.sort(rng.uniform(0, 540 * 24 * 60, n)), unit="m"
    )
    durations = np.clip(rng.lognormal(np.log(60), 0.8, n), 5, 60 * 48)
    profit_ratio = rng.normal(0.004, 0.02, n)  # edge with realistic noise
    stake = np.full(n, 100.0)
    exit_reasons = rng.choice(
        ["exit_1h_trend_reversal", "exit_trend_exhaustion",
         "exit_momentum_breakdown", "trailing_stop_loss", "stop_loss"],
        size=n, p=[0.3, 0.25, 0.2, 0.15, 0.1],
    )
    return pd.DataFrame({
        "pair": rng.choice(["BTC/USDT", "ETH/USDT"], size=n),
        "open_date": opens,
        "close_date": opens + pd.to_timedelta(durations, unit="m"),
        "profit_ratio": profit_ratio,
        "profit_abs": profit_ratio * stake,
        "stake_amount": stake,
        "exit_reason": exit_reasons,
        "enter_tag": "trend_following",
        "stop_distance_pct": np.round(rng.uniform(0.01, 0.05, n), 4),
    })


def synthetic_daily(seed: int = 11) -> dict:
    """Two years of daily candles per pair: up-trend, chop, down-trend."""
    rng = np.random.default_rng(seed)
    frames = {}
    for pair, drift_scale in (("BTC/USDT", 1.0), ("ETH/USDT", 1.3)):
        n = 730
        drift = np.concatenate([
            np.full(n // 3, 0.004), np.full(n // 3, 0.0),
            np.full(n - 2 * (n // 3), -0.003),
        ]) * drift_scale
        returns = drift + rng.normal(0, 0.02, n)
        close = 100 * np.exp(np.cumsum(returns))
        open_ = np.concatenate(([100.0], close[:-1]))
        wick = np.abs(rng.normal(0, 0.008, n))
        frames[pair] = pd.DataFrame({
            "date": pd.date_range("2023-06-01", periods=n, freq="1D", tz="UTC"),
            "open": open_,
            "high": np.maximum(open_, close) * (1 + wick),
            "low": np.minimum(open_, close) * (1 - wick),
            "close": close,
            "volume": rng.lognormal(10, 0.3, n),
        })
    return frames


# ------------------------------------------------------------------ sections

def test_loaders(trades: pd.DataFrame, scratch: Path) -> None:
    print("\n[1] loaders")
    normalized = loaders.ensure_trades(trades)
    check("canonical schema accepted", len(normalized) == len(trades))
    check("trade_duration derived",
          "trade_duration" in normalized.columns
          and normalized["trade_duration"].gt(0).all())
    try:
        loaders.ensure_trades(trades.drop(columns=["pair"]))
        check("missing required column rejected", False)
    except ValueError:
        check("missing required column rejected", True)
    # JSON fallback fixture (documented export structure)
    fixture = scratch / "fixture_export.json"
    records = json.loads(trades.to_json(orient="records", date_format="iso"))
    fixture.write_text(json.dumps(
        {"strategy": {"AdaptiveTrendStrategy": {"trades": records}}}),
        encoding="utf-8")
    loaded = loaders.load_backtest_export(fixture)
    check("JSON fallback parses export structure", len(loaded) == len(trades))


def test_metrics(trades: pd.DataFrame) -> dict:
    print("\n[2] metrics")
    summary = metrics.summarize(trades, 1000.0)
    expected_keys = {"cagr", "sharpe", "sortino", "profit_factor", "win_rate",
                     "expectancy", "max_drawdown_pct", "recovery_factor",
                     "calmar", "trades", "quarterly_positive_share"}
    check("full metric battery present",
          expected_keys.issubset(summary), str(expected_keys - set(summary)))
    check("metric values finite and sane",
          0 <= summary["win_rate"] <= 1 and summary["trades"] == len(trades)
          and 0 <= summary["max_drawdown_pct"] < 1)

    holding = metrics.holding_time_stats(trades, by="exit_reason")
    check("holding-time percentiles present",
          all(k in holding for k in
              ("mean", "median", "p25", "p75", "p90", "max")))
    check("holding-time ordering p25<=median<=p75<=p90<=max",
          holding["p25"] <= holding["median"] <= holding["p75"]
          <= holding["p90"] <= holding["max"])
    check("holding breakdown by exit reason", "breakdown" in holding
          and len(holding["breakdown"]) >= 3)

    known = pd.DataFrame({
        "pair": ["A/U"] * 4,
        "open_date": pd.date_range("2024-01-01", periods=4, freq="1D", tz="UTC"),
        "close_date": pd.date_range("2024-01-01 01:00", periods=4, freq="1D",
                                    tz="UTC"),
        "profit_ratio": [0.10, -0.05, 0.10, -0.05],
        "profit_abs": [10.0, -5.0, 10.0, -5.0],
        "stake_amount": [100.0] * 4,
    })
    check("profit factor exact on known data",
          abs(metrics.profit_factor(known) - 2.0) < 1e-9)
    check("max consecutive losses exact",
          metrics.max_consecutive_losses(known) == 1)
    return summary


def test_portfolio(trades: pd.DataFrame, daily: dict) -> dict:
    print("\n[3] portfolio")
    result = portfolio.analyze(trades, 1000.0, max_open_trades=99,
                               price_data=daily)
    for key in ("avg_simultaneous_positions", "max_simultaneous_positions",
                "peak_exposure_pct", "capital_utilization_pct",
                "idle_capital_pct", "portfolio_max_drawdown_pct",
                "worst_case_simultaneous_stop_pct_assumed", "co_holding"):
        check(f"portfolio metric '{key}' present", key in result)
    check("utilization + idle = 1",
          abs(result["capital_utilization_pct"]
              + result["idle_capital_pct"] - 1.0) < 1e-6)
    check("max positions >= avg positions",
          result["max_simultaneous_positions"]
          >= result["avg_simultaneous_positions"])
    check("co-holding correlation computed with price data",
          result["co_holding"]["status"].startswith("computed"))

    # hand-built overlap: two trades open together, stops 6% assumed
    tiny = pd.DataFrame({
        "pair": ["A/U", "B/U"],
        "open_date": pd.to_datetime(["2024-01-01 00:00", "2024-01-01 01:00"],
                                    utc=True),
        "close_date": pd.to_datetime(["2024-01-01 03:00", "2024-01-01 02:00"],
                                     utc=True),
        "profit_ratio": [0.01, 0.01],
        "profit_abs": [1.0, 1.0],
        "stake_amount": [100.0, 100.0],
    })
    tiny_result = portfolio.analyze(loaders.ensure_trades(tiny), 1000.0, 3)
    check("hand-built overlap: max 2 concurrent",
          tiny_result["max_simultaneous_positions"] == 2)
    check("hand-built worst-case stop = 1.2% of capital",
          abs(tiny_result["worst_case_simultaneous_stop_pct_assumed"]
              - 0.012) < 1e-9,
          str(tiny_result["worst_case_simultaneous_stop_pct_assumed"]))
    check("no-price-data correlation marked pending",
          tiny_result["co_holding"]["status"].startswith("pending"))
    return result


def test_monte_carlo(trades: pd.DataFrame) -> monte_carlo.MonteCarloResult:
    print("\n[4] monte carlo")
    result = monte_carlo.run(trades, 1000.0, n_resamples=2000, seed=1)
    for metric in ("cagr", "sharpe", "max_drawdown", "expectancy",
                   "profit_factor"):
        check(f"CI present for {metric}",
              metric in result.ci and "p2.5" in result.ci[metric])
    ci = result.ci["expectancy"]
    check("CI ordering p2.5<=median<=p97.5",
          ci["p2.5"] <= ci["median"] <= ci["p97.5"])
    check("gates evaluated", "all_pass" in result.gates)
    check("losing-streak distribution present",
          result.losing_streak.get("mc_p99", 0)
          >= result.losing_streak.get("realized_max", 0) - 1)
    again = monte_carlo.run(trades, 1000.0, n_resamples=2000, seed=1)
    check("deterministic under fixed seed", again.ci == result.ci)
    return result


# Sample tunable-parameter bundles for the sensitivity mechanism. These belong
# to the TEST, not to any market: build_grid perturbs whatever frozen dataclass
# instances it is handed, so the test owns a representative fixture instead of
# importing a strategy's settings.
@dataclass(frozen=True)
class _SampleEngine:
    adx_min_1h: float = 22.0
    adx_min_15m: float = 20.0
    rsi_min: float = 50.0
    rsi_max: float = 75.0
    rsi_ideal: float = 60.0
    volume_ratio_min: float = 1.2
    atr_pct_min: float = 0.0010
    atr_pct_max: float = 0.025
    min_quote_volume: float = 50_000.0
    max_spread_pct: float = 0.0015
    min_score: float = 0.60
    weight_rsi: float = 0.40
    weight_volume: float = 0.35
    weight_volatility: float = 0.25


@dataclass(frozen=True)
class _SampleRisk:
    hard_stop_pct: float = 0.06
    atr_stop_multiplier: float = 2.0
    trail_atr_multiplier: float = 2.0
    trail_activation_profit: float = 0.006
    reward_atr_multiple: float = 3.0
    min_risk_reward: float = 1.3
    risk_per_trade: float = 0.01
    exit_adx_floor: float = 18.0
    exit_rsi_floor: float = 40.0
    enable_risk_sizing: bool = False  # toggle: must be skipped by the grid
    profit_lock_tiers: tuple = ((0.010, 0.001), (0.018, 0.008),
                                (0.028, 0.015), (0.045, 0.028))


@dataclass(frozen=True)
class _SampleIndicators:
    base_ema_fast: int = 9
    base_ema_slow: int = 21
    adx_period: int = 14
    rsi_period: int = 14
    atr_period: int = 14
    volume_window: int = 20
    structure_window: int = 10
    trend_shift: int = 6


def _sample_settings() -> dict:
    return {"engine": _SampleEngine(), "risk": _SampleRisk(),
            "indicators": _SampleIndicators()}


def test_sensitivity(scratch: Path) -> None:
    print("\n[5] sensitivity")
    grid = sensitivity.build_grid(_sample_settings())
    check("grid non-trivial (>100 points)", len(grid) > 100, str(len(grid)))
    params = {p.param for p in grid}
    for expected in ("adx_min_1h", "min_score", "atr_stop_multiplier",
                     "hard_stop_pct", "exit_adx_floor", "base_ema_fast",
                     "profit_lock_tiers", "weight_rsi"):
        check(f"grid covers '{expected}'", expected in params)
    check("no boolean toggles in grid", "enable_risk_sizing" not in params)
    hard = [p for p in grid if p.param == "hard_stop_pct" and p.multiplier > 1]
    check("hard_stop capped at 6% above baseline",
          all(p.value <= 0.06 + 1e-12 for p in hard))
    ints = [p for p in grid if p.param == "base_ema_fast"]
    check("integer periods change at every multiplier",
          all(p.value != p.base_value for p in ints))
    manifest = sensitivity.write_manifest(grid, scratch / "sens_manifest.json")
    check("manifest written", manifest.exists())
    commands = sensitivity.plan_backtests(
        grid[:10], "20210701-20230630", scratch / "overrides")
    check("backtest commands planned (not executed)",
          len(commands) > 0 and all(c.startswith("docker compose run")
                                    for c in commands))

    plateau_rows, cliff_rows = [], []
    for mult in (0.8, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2):
        plateau_rows.append({"param": "stable_param", "multiplier": mult,
                             "sharpe": 1.5 - abs(1 - mult),
                             "profit_factor": 1.6})
        cliff_rows.append({"param": "fragile_param", "multiplier": mult,
                           "sharpe": 1.8 if mult == 1.0 else 0.2,
                           "profit_factor": 1.8 if mult == 1.0 else 0.9})
    stable = sensitivity.aggregate(plateau_rows)
    fragile = sensitivity.aggregate(cliff_rows)
    check("plateau surface accepted", stable["verdict"].startswith("plateau"))
    check("cliff surface rejected", fragile["verdict"].startswith("cliff"))
    check("tornado ranks fragile above stable",
          fragile["tornado"][0]["param"] == "fragile_param")


def test_walkforward(trades: pd.DataFrame) -> None:
    print("\n[6] walk-forward")
    quarterly = walkforward.generate_folds("2020-01-01", "2025-06-30",
                                           "quarterly")
    monthly = walkforward.generate_folds("2020-01-01", "2025-06-30", "monthly")
    semi = walkforward.generate_folds("2020-01-01", "2025-06-30", "semiannual")
    check("quarterly = 18 folds", len(quarterly) == 18, str(len(quarterly)))
    check("monthly = 54 folds", len(monthly) == 54, str(len(monthly)))
    check("semi-annual = 9 folds", len(semi) == 9, str(len(semi)))
    first = quarterly[0]
    check("first OOS starts 12 months after dev start",
          first.oos_start.isoformat().startswith("2021-01-01"))
    check("warmup precedes IS by 1 month",
          first.warmup_start.isoformat().startswith("2020-12-01")
          or first.warmup_start < first.is_start)
    check("timerange format",
          first.timerange("oos") == "20210101-20210401",
          first.timerange("oos"))
    commands = walkforward.plan_mode_a(quarterly[:3])
    check("Mode A commands planned", len(commands) == 3)

    thirds = np.array_split(np.arange(len(trades)), 3)
    fold_frames = [trades.iloc[idx] for idx in thirds] + [pd.DataFrame()]
    aggregated = walkforward.aggregate_mode_a(fold_frames, 1000.0)
    check("aggregate stitches folds (incl. empty fold)",
          aggregated["aggregate"]["trades"] == len(trades))
    check("per-fold table has one row per fold",
          len(aggregated["per_fold"]) == 4)

    check("WFE computed", walkforward.walk_forward_efficiency(0.10, 0.06) == 0.6)
    check("WFE undefined for non-positive IS",
          walkforward.walk_forward_efficiency(0.0, 0.05) is None)

    scores = walkforward.score_cadences({
        "semiannual": {"sharpe_lower_ci": 1.0, "wfe": 0.8,
                       "param_stability_ok": True},
        "quarterly": {"sharpe_lower_ci": 1.05, "wfe": 0.7,
                      "param_stability_ok": True},
        "monthly": {"sharpe_lower_ci": 1.5, "wfe": 0.3,
                    "param_stability_ok": False},
    })
    check("parsimony: slowest wins without a qualified beat",
          scores["selected"] == "semiannual", str(scores["selected"]))
    scores2 = walkforward.score_cadences({
        "semiannual": {"sharpe_lower_ci": 0.8, "wfe": 0.6,
                       "param_stability_ok": True},
        "quarterly": {"sharpe_lower_ci": 1.2, "wfe": 0.7,
                      "param_stability_ok": True},
    })
    check("qualified faster cadence can win",
          scores2["selected"] == "quarterly", str(scores2["selected"]))
    try:
        walkforward.NotImplementedOptimizer().fit(quarterly[0])
        check("Mode B optimizer explicitly not implemented", False)
    except NotImplementedError:
        check("Mode B optimizer explicitly not implemented", True)


def test_regime(trades: pd.DataFrame, daily: dict) -> dict:
    print("\n[7] regime")
    labels = {pair: regime.label_daily(frame) for pair, frame in daily.items()}
    btc = labels["BTC/USDT"]
    check("labels cover trend axis",
          set(btc["trend_label"].unique()) <= {"bull", "bear", "range"})
    check("labels cover vol axis",
          set(btc["vol_label"].unique())
          <= {"high_volatility", "normal_volatility", "low_volatility"})
    third = len(btc) // 3
    up_segment = btc.iloc[60:third]
    down_segment = btc.iloc[-third + 60:]
    check("engineered up-trend mostly labelled bull",
          (up_segment["trend_label"] == "bull").mean() > 0.5,
          f"{(up_segment['trend_label'] == 'bull').mean():.2f}")
    check("engineered down-trend mostly labelled bear",
          (down_segment["trend_label"] == "bear").mean() > 0.5,
          f"{(down_segment['trend_label'] == 'bear').mean():.2f}")

    tagged = regime.tag_trades(trades, labels)
    check("every trade tagged",
          tagged["entry_trend_regime"].isin(
              ["bull", "bear", "range", "unlabelled"]).all())
    breakdown = regime.regime_breakdown(tagged, 1000.0)
    check("breakdown has trend/vol/matrix",
          all(k in breakdown for k in ("by_trend", "by_vol", "matrix")))
    check("stability gate evaluated",
          "pass" in breakdown.get("stability_gate", {}))
    check("cross-regime share reported",
          0 <= breakdown["cross_regime_share"] <= 1)
    return breakdown


def test_stress(trades: pd.DataFrame,
                mc_result: monte_carlo.MonteCarloResult) -> dict:
    print("\n[8] stress")
    baseline = metrics.summarize(trades, 1000.0)
    doubled = stress.with_extra_fees(trades)
    check("double fees reduce every trade's profit",
          (doubled["profit_ratio"] < trades["profit_ratio"]).all())
    check("fee delta exact (-0.2% round trip)",
          abs((trades["profit_ratio"] - doubled["profit_ratio"])
              .iloc[0] - 0.002) < 1e-12)
    slipped = stress.with_slippage(trades, bps_per_side=7.5)
    check("slippage delta exact (-15 bps round trip)",
          abs((trades["profit_ratio"] - slipped["profit_ratio"])
              .iloc[0] - 0.0015) < 1e-12)
    result = stress.run_all(trades, 1000.0, mc_result.losing_streak)
    for key in ("baseline", "double_fees", "slippage", "losing_streak",
                "volatility_spike_windows", "delayed_exits"):
        check(f"stress scenario '{key}' present", key in result)
    check("stressed profit < baseline profit",
          result["double_fees"]["net_profit_abs"]
          < baseline["net_profit_abs"])
    try:
        stress.delayed_exits(trades, None)
        check("delayed exits raises without price data", False)
    except NotImplementedError:
        check("delayed exits raises without price data", True)
    return result


def test_report_and_coordinator(trades: pd.DataFrame, daily: dict,
                                scratch: Path) -> None:
    print("\n[9] report + coordinator")
    out = scratch / "selftest_report.md"
    options = coordinator.ValidationOptions(
        start_capital=1000.0,
        max_open_trades=99,
        mc_resamples=2000,
        report_title="Selftest Validation Report",
        context_note="SELF-TEST on synthetic data - verdict is NOT a "
                     "strategy judgment.",
        daily_ohlcv=daily,
        next_action="Tooling self-test only; next real step is the "
                    "Pipeline Smoke Test (VALIDATION_RULES SS18).",
    )
    results = coordinator.run_validation(
        trades, out, options, log_jsonl=scratch / "selftest_log.jsonl")
    check("coordinator ran end-to-end", "report_path" in results)
    check("verdict computed",
          results["verdict"]["status"] in ("PASS", "FAIL", "INCONCLUSIVE"),
          results["verdict"]["status"])
    text = out.read_text(encoding="utf-8")
    missing = [t for t in report_mod.SECTION_TITLES if f" {t}" not in text]
    check("all 18 report sections present", not missing, str(missing))
    check("regime section populated (not pending)",
          "by_trend" in text)
    check("pending sections explicitly marked",
          "PENDING" in text)  # sensitivity is not supplied here
    log_lines = (scratch / "selftest_log.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    parsed = [json.loads(line) for line in log_lines]
    steps = {p["step"] for p in parsed}
    check("structured JSONL logs for every step",
          {"load_trades", "metrics", "holding_time", "portfolio",
           "monte_carlo", "stress", "regime", "report"} <= steps,
          str(steps))
    check("log records carry start+end events",
          any(p["event"] == "start" for p in parsed)
          and any(p["event"] == "end" for p in parsed))


def main() -> int:
    print("=" * 64)
    print("Validation tooling self-test (no market data)")
    print("=" * 64)
    scratch = Path(tempfile.mkdtemp(prefix="algo_validation_selftest_"))
    configure(scratch / "selftest_log.jsonl")

    trades = loaders.ensure_trades(synthetic_trades())
    daily = synthetic_daily()

    test_loaders(trades, scratch)
    test_metrics(trades)
    test_portfolio(trades, daily)
    mc_result = test_monte_carlo(trades)
    test_sensitivity(scratch)
    test_walkforward(trades)
    test_regime(trades, daily)
    test_stress(trades, mc_result)
    test_report_and_coordinator(trades, daily, scratch)

    print("\n" + "=" * 64)
    if FAILURES:
        print(f"RESULT: FAIL - {len(FAILURES)} failed check(s):")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("RESULT: PASS - all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
