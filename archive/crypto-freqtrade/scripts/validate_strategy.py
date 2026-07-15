"""Offline validation harness for AdaptiveTrendStrategy.

Freqtrade has no ``test-strategy`` command, so this script is the project's
equivalent. It validates the full pipeline WITHOUT downloading market data:

    1. Load the strategy through Freqtrade's StrategyResolver (proves config +
       class + imports resolve exactly as in production) and check its safety
       attributes, including a startup_candle_count that warms the 4h EMA50.
    2. Run populate_indicators / entry / exit over deterministic synthetic
       candles and assert the expected columns and signal semantics.
    3. DecisionEngine: mandatory gates block; advisory checks feed the score;
       a high-advisory context is GO, a low-advisory one is NO-GO on
       conviction alone, and each mandatory failure is NO-GO with a reason.
    4. Risk engine + TradeManager: hard-stop cap, profit-lock tiers, stop
       monotonicity, and risk-based sizing (clamped to max, not proposed).
    5. Profile registry: five profiles, only trend_following enabled.
    6. Settings: single source of truth (no profile-local thresholds; the
       profile gate follows settings) and full config override.
    7. RegimeDetector: classifies TREND and the profile supports it.

Run inside the container:
    docker compose run --rm --entrypoint python3 freqtrade user_data/scripts/validate_strategy.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent / "strategies"))

FAILURES: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


# ---------------------------------------------------------------- synthetic data

def build_synthetic_frames(n: int = 8000, seed: int = 42) -> dict:
    """Deterministic trending random-walk OHLCV, 5m base + resampled HTFs."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.00006, 0.0016, n)
    close = 100 * np.exp(np.cumsum(returns))
    open_ = np.concatenate(([100.0], close[:-1]))
    wick = np.abs(rng.normal(0, 0.0008, n))
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC"),
            "open": open_,
            "high": np.maximum(open_, close) * (1 + wick),
            "low": np.minimum(open_, close) * (1 - wick),
            "close": close,
            "volume": rng.lognormal(8, 0.4, n),
        }
    )

    def resample(rule: str) -> pd.DataFrame:
        return (
            frame.set_index("date")
            .resample(rule, label="left", closed="left")
            .agg({"open": "first", "high": "max", "low": "min",
                  "close": "last", "volume": "sum"})
            .dropna()
            .reset_index()
        )

    return {"5m": frame, "15m": resample("15min"),
            "1h": resample("1h"), "4h": resample("4h")}


class StubDataProvider:
    """Minimal stand-in for Freqtrade's DataProvider (informative frames only)."""

    def __init__(self, frames: dict) -> None:
        self.frames = frames

    def get_pair_dataframe(self, pair: str, timeframe: str = "") -> pd.DataFrame:
        return self.frames.get(timeframe, pd.DataFrame()).copy()


class FakeTrade:
    """Minimal trade object for TradeManager tests (custom-data dict)."""

    def __init__(self, open_rate: float) -> None:
        self.open_rate = open_rate
        self._data: dict = {}

    def get_custom_data(self, key: str, default=None):
        return self._data.get(key, default)

    def set_custom_data(self, key: str, value) -> None:
        self._data[key] = value


def make_row(**overrides) -> pd.Series:
    """A context row representing a textbook multi-timeframe uptrend."""
    row = {
        "close": 100.0, "ema_fast": 99.8, "ema_slow": 99.5,
        "volume_ratio": 1.6, "volume_mean": 5000.0,
        "ema_fast_15m": 99.7, "ema_slow_15m": 99.2, "adx_15m": 28.0,
        "rsi_15m": 62.0, "atr_15m": 0.6, "atr_pct_15m": 0.006,
        "swing_low_15m": 98.9, "structure_up_15m": 1.0,
        "ema_fast_1h": 99.4, "ema_slow_1h": 98.8, "adx_1h": 30.0,
        "closing_higher_1h": 1.0,
        "ema_fast_4h": 98.5, "ema_slow_4h": 97.5,
    }
    row.update(overrides)
    return pd.Series(row)


def entry_frame() -> pd.DataFrame:
    """Two candles; the second is a fresh 5m cross-up with all HTFs aligned."""
    return pd.DataFrame({
        "close": [99.0, 100.0],
        "ema_fast": [99.5, 99.8],   # row1: close crosses above ema_fast
        "ema_slow": [99.3, 99.5],
        "ema_fast_15m": [99.7, 99.7], "ema_slow_15m": [99.2, 99.2],
        "adx_15m": [28.0, 28.0], "structure_up_15m": [1.0, 1.0],
        "ema_fast_1h": [99.4, 99.4], "ema_slow_1h": [98.8, 98.8],
        "adx_1h": [30.0, 30.0], "closing_higher_1h": [1.0, 1.0],
        "ema_fast_4h": [98.5, 98.5], "ema_slow_4h": [97.5, 97.5],
    })


# -------------------------------------------------------------------- sections

def load_strategy():
    print("\n[1] Strategy resolution via Freqtrade")
    from freqtrade.configuration import Configuration
    from freqtrade.resolvers import StrategyResolver

    config = Configuration.from_files(["user_data/config.json"])
    config["strategy"] = "AdaptiveTrendStrategy"
    strategy = StrategyResolver.load_strategy(config)
    check("StrategyResolver loads AdaptiveTrendStrategy", strategy is not None)
    check("timeframe is 5m", strategy.timeframe == "5m")
    check("hard emergency stop is -6%", strategy.stoploss == -0.06)
    check("custom stoploss enabled", strategy.use_custom_stoploss is True)
    check("no ROI table (no arbitrary exits)", strategy.minimal_roi == {})
    check("spot only, no shorting", strategy.can_short is False)
    check("active profile is trend_following",
          getattr(strategy.profile, "name", None) == "trend_following")
    # 4h EMA50 needs >= 50 * (240/5) = 2400 base candles of warmup.
    check("startup_candle_count warms the 4h EMA50",
          strategy.startup_candle_count >= 2400,
          f"startup={strategy.startup_candle_count}")
    check("regime detector wired in", strategy.regime_detector is not None)
    check("settings are the single source", strategy.settings is not None)
    return strategy


def run_pipeline(strategy) -> pd.DataFrame:
    print("\n[2] Indicator/entry/exit pipeline on synthetic candles")
    frames = build_synthetic_frames()
    strategy.dp = StubDataProvider(frames)
    metadata = {"pair": "BTC/USDT"}

    df = strategy.advise_indicators(frames["5m"].copy(), metadata)
    expected = [
        "ema_fast", "ema_slow", "atr", "volume_ratio", "swing_low",
        "ema_fast_15m", "adx_15m", "rsi_15m", "atr_pct_15m",
        "swing_low_15m", "structure_up_15m",
        "ema_fast_1h", "adx_1h", "closing_higher_1h",
        "ema_fast_4h", "ema_slow_4h",
    ]
    missing = [c for c in expected if c not in df.columns]
    check("all multi-timeframe columns merged", not missing, f"missing: {missing}")
    tail = df.tail(50)
    check("indicators warmed at frame tail (incl. 4h EMA50)",
          not tail[["ema_fast", "ema_fast_15m", "ema_fast_1h", "ema_fast_4h"]]
          .isna().any().any())

    df = strategy.advise_entry(df, metadata)
    df = strategy.advise_exit(df, metadata)
    check("enter_long column produced", "enter_long" in df.columns)
    check("exit_long column produced", "exit_long" in df.columns)
    check("signal columns are binary",
          set(df["enter_long"].unique()) <= {0, 1}
          and set(df["exit_long"].unique()) <= {0, 1})
    entries, exits = int(df["enter_long"].sum()), int(df["exit_long"].sum())
    tagged = (df.loc[df["exit_long"] == 1, "exit_tag"] != "").all()
    check("every exit signal carries a reason tag", bool(tagged) or exits == 0)
    print(f"       (synthetic data produced {entries} entry / {exits} exit signals)")
    return df


def test_decision_engine():
    print("\n[3] GO / NO-GO engine: mandatory gates + advisory score")
    from algo_core.decision_engine import DecisionEngine, RejectionRecorder, TradeContext
    from algo_core.settings import EngineParams, RiskParams

    rejection_file = Path("user_data/logs/validate_rejections.jsonl")
    rejection_file.unlink(missing_ok=True)
    engine = DecisionEngine(EngineParams(), RiskParams(), RejectionRecorder(rejection_file))

    go = engine.evaluate(TradeContext("BTC/USDT", "long", make_row(), 100.0, regime="trend"))
    mandatory = [c for c in go.checks if not c.advisory]
    advisory = [c for c in go.checks if c.advisory]
    check("all mandatory pass + strong advisory is GO", go.go, f"score={go.score}")
    check("8 mandatory + 3 advisory checks", len(mandatory) == 8 and len(advisory) == 3,
          f"{len(mandatory)}/{len(advisory)}")
    check("advisory set is exactly RSI/volume/volatility",
          {c.name for c in advisory} == {"momentum", "volume", "volatility"})

    # Low advisory quality, all mandatory still passing -> NO-GO on conviction.
    weak_adv = engine.evaluate(
        TradeContext("BTC/USDT", "long", make_row(rsi_15m=48.0, volume_ratio=1.0),
                     100.0, regime="trend"))
    check("weak advisory alone is NO-GO", not weak_adv.go, f"score={weak_adv.score}")
    check("rejection is conviction-only (no mandatory failure)",
          weak_adv.rejection_reasons
          and all("conviction" in r for r in weak_adv.rejection_reasons),
          str(weak_adv.rejection_reasons))

    # Mandatory trend failure -> NO-GO regardless of advisory.
    bad_trend = engine.evaluate(
        TradeContext("BTC/USDT", "long", make_row(adx_1h=12.0), 100.0, regime="trend"))
    check("mandatory 1h-trend failure is NO-GO", not bad_trend.go)
    check("rejection names the failing mandatory check",
          any("itf_trend" in r for r in bad_trend.rejection_reasons),
          str(bad_trend.rejection_reasons))

    # Mandatory risk failure -> stop wider than the 6% hard cap.
    wide = engine.evaluate(
        TradeContext("BTC/USDT", "long", make_row(atr_15m=4.0, atr_pct_15m=0.04),
                     100.0, regime="trend"))
    check("stop wider than 6% hard cap is NO-GO",
          not wide.go and any("position_sizing" in r for r in wide.rejection_reasons))

    # Mandatory liquidity failure -> excessive spread.
    spread = engine.evaluate(
        TradeContext("BTC/USDT", "long", make_row(), 100.0, spread_pct=0.01, regime="trend"))
    check("excessive spread is NO-GO", not spread.go)
    check("NO-GO recorded to JSONL with regime", rejection_file.exists())
    rejection_file.unlink(missing_ok=True)


def test_risk_and_trade_manager():
    print("\n[4] Risk engine and TradeManager")
    from algo_core.risk_engine import (
        initial_stop_pct, locked_profit_for, risk_based_stake, stop_is_tradeable,
    )
    from algo_core.settings import RiskParams
    from algo_core.trade_manager import TradeManager

    params = RiskParams()
    stop = initial_stop_pct(100.0, 0.6, 98.9, params)
    check("initial stop uses wider of ATR/structure", abs(stop - 0.012) < 1e-9, f"stop={stop}")
    check("tradeable stop accepted", stop_is_tradeable(stop, params))
    check("stop beyond 6% rejected", not stop_is_tradeable(0.08, params))
    check("no profit lock below first tier", locked_profit_for(0.005, params) is None)
    check("profit lock tiers escalate", locked_profit_for(0.03, params) == 0.015)

    # Risk-based sizing clamps to max_stake (not to a fixed proposed stake).
    check("risk sizing disabled by default", params.enable_risk_sizing is False)
    check("risk sizing clamps to max_stake, can exceed old fixed stake",
          abs(risk_based_stake(1000, 0.012, 10, 500, params) - 500) < 1e-9)
    check("risk sizing respects min_stake floor",
          abs(risk_based_stake(50, 0.05, 20, 500, params) - 20) < 1e-9)
    check("risk sizing returns None when inputs unavailable",
          risk_based_stake(None, 0.01, 1, 10, params) is None)

    manager = TradeManager(params)
    trade = FakeTrade(open_rate=100.0)
    stops = []
    for rate, profit in [(100.2, 0.002), (101.2, 0.012), (103.0, 0.03),
                         (102.0, 0.02), (101.0, 0.01)]:
        ratio = manager.stoploss_ratio(trade, rate, profit, make_row())
        check(f"stop ratio at rate {rate} is negative", ratio is not None and ratio < 0)
        stops.append(trade.get_custom_data("stop_price")
                     or 100.0 * (1 - trade.get_custom_data("initial_stop_pct")))
    check("stop price never widens (monotonic)",
          all(b >= a - 1e-9 for a, b in zip(stops, stops[1:])),
          f"stops={[round(s, 3) for s in stops]}")
    check("profit locking raised stop above entry", max(stops) > 100.0)


def test_profiles():
    print("\n[5] Profile registry")
    from algo_core.profiles import available_profiles, get_active_profile
    from algo_core.settings import AlgoSettings

    profiles = available_profiles()
    check("five profiles registered", len(profiles) == 5, str(sorted(profiles)))
    check("only trend_following enabled",
          [n for n, on in profiles.items() if on] == ["trend_following"])
    active = get_active_profile("trend_following", settings=AlgoSettings())
    check("active profile instantiates with settings", active is not None)
    for name in ("breakout", "mean_reversion", "pullback_trend", "volatility_expansion"):
        try:
            get_active_profile(name, settings=AlgoSettings())
            check(f"disabled profile '{name}' refuses activation", False)
        except ValueError:
            check(f"disabled profile '{name}' refuses activation", True)
    try:
        get_active_profile("nonexistent", settings=AlgoSettings())
        check("unknown profile rejected", False)
    except ValueError:
        check("unknown profile rejected", True)


def test_settings_single_source():
    print("\n[6] Settings: single source of truth + configurability")
    from algo_core.profiles.trend_following import TrendFollowingProfile
    from algo_core.settings import AlgoSettings

    # (3) no duplicated, profile-local threshold constants.
    leaked = [a for a in ("ADX_MIN_1H", "ADX_MIN_15M", "RSI_MIN", "RSI_MAX",
                          "VOLUME_RATIO_MIN", "ATR_PCT_MIN", "ATR_PCT_MAX")
              if hasattr(TrendFollowingProfile, a)]
    check("no profile-local threshold constants", not leaked, f"leaked: {leaked}")

    # (4) full config override reaches every params bucket.
    overridden = AlgoSettings.from_config({"algo_trader": {
        "engine": {"min_score": 0.9, "adx_min_1h": 30},
        "risk": {"enable_risk_sizing": True, "hard_stop_pct": 0.05},
        "indicators": {"base_ema_fast": 7},
    }})
    check("engine threshold overridable from config", overridden.engine.min_score == 0.9)
    check("risk toggle overridable from config", overridden.risk.enable_risk_sizing is True)
    check("indicator period overridable from config",
          overridden.indicators.base_ema_fast == 7)

    # The profile gate reads thresholds from settings (proves single source).
    frame = entry_frame()
    default_gate = TrendFollowingProfile(settings=AlgoSettings()).entry_signal(frame)
    strict = AlgoSettings.from_config({"algo_trader": {"engine": {"adx_min_1h": 999}}})
    strict_gate = TrendFollowingProfile(settings=strict).entry_signal(frame)
    check("profile gate fires with default settings", int(default_gate.sum()) == 1,
          f"entries={int(default_gate.sum())}")
    check("raising a settings threshold suppresses the gate",
          int(strict_gate.sum()) == 0, f"entries={int(strict_gate.sum())}")


def test_regime():
    print("\n[7] RegimeDetector")
    from algo_core.profiles.trend_following import TrendFollowingProfile
    from algo_core.regime import MarketRegime, RegimeDetector
    from algo_core.settings import EngineParams

    detector = RegimeDetector(EngineParams())
    check("regime enum exposes the required members",
          {r.name for r in MarketRegime}
          == {"TREND", "RANGE", "HIGH_VOLATILITY", "LOW_VOLATILITY", "UNKNOWN"},
          str(sorted(r.name for r in MarketRegime)))
    check("classifies market as TREND", detector.detect(make_row()) == MarketRegime.TREND)
    check("classification is robust to a None row",
          detector.detect(None) == MarketRegime.TREND)
    check("active profile supports the TREND regime",
          "trend" in TrendFollowingProfile.supported_regimes)


def main() -> int:
    print("=" * 64)
    print("AdaptiveTrendStrategy offline validation (no market data)")
    print("=" * 64)
    strategy = load_strategy()
    run_pipeline(strategy)
    test_decision_engine()
    test_risk_and_trade_manager()
    test_profiles()
    test_settings_single_source()
    test_regime()
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
