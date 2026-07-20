"""Adversarial verification (independent-verification phase, Part 3).

Pathological inputs aimed at breaking the execution engine and the twelve
intraday strategies: missing/duplicate/invalid/zero-volume bars, flash
crashes, gap cascades, degenerate sessions, boundary-length datasets, NaN
levels, cost extremes. Where a behaviour is a documented convention rather
than a bug, the test PINS it (with a comment) instead of "fixing" it.
"""

import numpy as np
import pandas as pd
import pytest

from algo.core.costs import FlatCostModel, NseCostParams, NseEquityCostModel, \
    Product
from algo.execution import ExecutionSpec, execute_signal, structural_intraday
from algo.research.validation import metrics
from algo.risk.engine import RiskParams
from algo.strategies.library import (
    CprBreakout, CprReversal, FirstPullbackAfterBreakout, GapAndGo,
    InsideBarBreakout, Nr7Intraday, OpeningRangeBreakout,
    PullbackContinuation15m, SupertrendContinuation, VolatilityExpansionBreakout1h,
    VwapPullback, VwapTrendContinuation,
)

COSTS = FlatCostModel(0.0)
PARAMS = RiskParams()

INTRADAY_STRATEGIES = [
    OpeningRangeBreakout, VwapTrendContinuation, VwapPullback, CprBreakout,
    FirstPullbackAfterBreakout, PullbackContinuation15m,
    VolatilityExpansionBreakout1h, GapAndGo, InsideBarBreakout,
    SupertrendContinuation, CprReversal, Nr7Intraday,
]


def _session(day, closes, highs=None, lows=None, opens=None, vols=None,
             freq="15min", start="09:15"):
    closes = np.asarray(closes, float)
    n = len(closes)
    return pd.DataFrame({
        "date": pd.date_range(f"{day} {start}", periods=n, freq=freq,
                              tz="UTC"),
        "open": np.asarray(opens, float) if opens is not None
        else (np.concatenate(([closes[0]], closes[:-1])) if n else
              np.array([], float)),
        "high": np.asarray(highs, float) if highs is not None else closes + 0.5,
        "low": np.asarray(lows, float) if lows is not None else closes - 0.5,
        "close": closes,
        "volume": np.asarray(vols, float) if vols is not None
        else np.full(n, 1000.0)})


def _concat(*frames):
    return pd.concat(frames, ignore_index=True)


WIDE = ExecutionSpec(stop_kind="column", stop_col="stop_level",
                     hard_stop_pct=None)


def _with_stop(frame, level):
    out = frame.copy()
    out["stop_level"] = level
    return out


def _run(bars, i, spec=WIDE, product=Product.INTRADAY, costs=COSTS):
    return execute_signal(bars, i, spec=spec, atr=1.0, swing_low=None,
                          cost_model=costs, product=product, stake=50_000.0,
                          params=PARAMS)


# ------------------------------------------------- degenerate dataset sizes

@pytest.mark.parametrize("cls", INTRADAY_STRATEGIES,
                         ids=lambda c: c.meta.name)
def test_strategies_survive_empty_single_and_tiny_frames(cls):
    strat = cls()
    for n in (0, 1, 2, 5):
        frame = _session("2024-03-04", [100.0] * n)
        prepared = strat.prepare(frame)
        signal = strat.entry_signal(prepared)
        assert len(signal) == n
        assert not signal.any()                   # never a signal without warmup


@pytest.mark.parametrize("cls", INTRADAY_STRATEGIES,
                         ids=lambda c: c.meta.name)
def test_strategies_survive_duplicate_and_unordered_history(cls):
    """Duplicate timestamps (upstream store prevents them, but the strategy
    layer must not crash) and a repeated flat tape."""
    day = _session("2024-03-04", [100.0] * 30)
    dup = _concat(day, day.iloc[10:12])           # duplicated bars appended
    strat = cls()
    signal = strat.entry_signal(strat.prepare(dup))
    assert len(signal) == len(dup)


def test_engine_single_and_two_bar_datasets():
    one = _with_stop(_session("2024-03-04", [100.0]), 90.0)
    assert _run(one, 0) is None                   # nothing left to manage
    two = _with_stop(_session("2024-03-04", [100.0, 100.5]), 90.0)
    trade = _run(two, 0)
    assert trade.exit_reason == "session_squareoff"
    assert trade.close_date == two["date"].iloc[1]


# ---------------------------------------------------- pathological sessions

def test_engine_handles_missing_bars_mid_session():
    """Holes in the 15m sequence (halts): dates, not positions, drive the
    session logic - the trade still squares off on the last PRESENT bar."""
    a = _session("2024-03-04", [100, 101, 102])
    b = _session("2024-03-04", [103, 104], start="13:15")   # 2h hole
    bars = _with_stop(_concat(a, b), 90.0)
    trade = _run(bars, 0)
    assert trade.exit_reason == "session_squareoff"
    assert trade.close_date == bars["date"].iloc[-1]


def test_engine_muhurat_like_two_bar_session():
    bars = _with_stop(_session("2024-11-01", [100, 100.5], start="18:15"),
                      90.0)
    trade = _run(bars, 0)
    assert trade.exit_reason == "session_squareoff"
    assert trade.holding_min == pytest.approx(15.0)


def test_engine_duplicate_timestamp_bars_do_not_crash():
    day = _session("2024-03-04", [100, 101, 101, 102])
    day.loc[2, "date"] = day.loc[1, "date"]       # duplicate timestamp
    trade = _run(_with_stop(day, 90.0), 0)
    assert trade.exit_reason == "session_squareoff"


# ------------------------------------------------------- hostile bar shapes

def test_engine_invalid_ohlc_bar_does_not_crash():
    bars = _session("2024-03-04", [100, 100, 100, 100])
    bars.loc[2, ["open", "high", "low"]] = [99.0, 98.0, 101.0]   # high < low
    trade = _run(_with_stop(bars, 90.0), 0)
    assert trade is not None                      # deterministic, no raise


def test_engine_flash_crash_fills_honestly():
    # crash bar OPENS at -20%: honest fill at the open, not the stop
    closes = [100, 100, 82, 85]
    opens = [100, 100, 80, 82]
    lows = [99.5, 99.5, 78, 81]
    highs = [100.5, 100.5, 83, 86]
    bars = _with_stop(_session("2024-03-04", closes, highs, lows, opens), 95.0)
    trade = _run(bars, 1)
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_price == pytest.approx(80.0)
    assert trade.gap_fill
    assert trade.profit_ratio == pytest.approx(80.0 / 100.0 - 1.0)


def test_engine_insane_bar_spanning_stop_and_target_is_a_stop():
    """A bar that gaps ABOVE the target yet trades below the stop: the
    pessimistic stop-first convention is pinned."""
    bars = _session("2024-03-04", [100, 100, 100, 100],
                    highs=[100.5, 100.5, 120, 100.5],
                    lows=[99.5, 99.5, 80, 99.5],
                    opens=[100, 100, 115, 100])
    bars["stop_level"] = 95.0
    bars["target_level"] = 110.0
    spec = ExecutionSpec(stop_kind="column", stop_col="stop_level",
                         hard_stop_pct=None, target_kind="column",
                         target_col="target_level")
    trade = _run(bars, 1, spec=spec)
    assert trade.exit_reason == "stop_loss"       # documented pessimism


def test_engine_nan_bar_mid_trade_does_not_poison_the_trade():
    bars = _session("2024-03-04", [100, 100, np.nan, 101, 101.5])
    bars.loc[2, ["open", "high", "low"]] = np.nan
    trade = _run(_with_stop(bars, 90.0), 0)
    assert trade.exit_reason == "session_squareoff"
    assert np.isfinite(trade.profit_abs)
    assert np.isfinite(trade.mfe_pct) and np.isfinite(trade.mae_pct)


def test_engine_zero_volume_bars_are_irrelevant_to_execution():
    bars = _with_stop(_session("2024-03-04", [100, 101, 102],
                               vols=[0, 0, 0]), 90.0)
    trade = _run(bars, 0)
    assert trade.exit_reason == "session_squareoff"


def test_engine_zero_atr_makes_atr_structure_signals_untradeable():
    from algo.execution import atr_trail_intraday
    bars = _session("2024-03-04", [100, 100, 100])
    trade = execute_signal(bars, 0, spec=atr_trail_intraday(), atr=0.0,
                           swing_low=None, cost_model=COSTS,
                           product=Product.INTRADAY, stake=50_000.0,
                           params=PARAMS)
    assert trade is None                          # honest skip, not a crash


def test_engine_nan_stop_level_skips_the_signal():
    bars = _with_stop(_session("2024-03-04", [100, 101, 102]), np.nan)
    assert _run(bars, 0) is None


# ------------------------------------------------- gaps and multi-day swing

def test_swing_gap_through_stop_fills_at_open_across_days():
    d1 = _session("2024-03-04", [100, 101])
    d2 = _session("2024-03-05", [92, 93], opens=[91, 92],
                  highs=[93, 94], lows=[90.5, 91.5])
    bars = _with_stop(_concat(d1, d2), 96.0)
    spec = ExecutionSpec(stop_kind="column", stop_col="stop_level",
                         hard_stop_pct=None, intraday=False,
                         allow_overnight=True, max_hold_bars=10)
    trade = _run(bars, 0, spec=spec, product=Product.DELIVERY)
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_price == pytest.approx(91.0)      # day-2 open
    assert trade.gap_fill


def test_swing_survives_consecutive_gap_days():
    d1 = _session("2024-03-04", [100, 100.5])
    d2 = _session("2024-03-05", [97, 97.5], opens=[96.8, 97],
                  lows=[96.5, 96.8], highs=[97.6, 98])
    d3 = _session("2024-03-06", [92, 92.5], opens=[91.5, 92],
                  lows=[91, 91.8], highs=[92.8, 93])
    bars = _with_stop(_concat(d1, d2, d3), 93.5)
    spec = ExecutionSpec(stop_kind="column", stop_col="stop_level",
                         hard_stop_pct=None, intraday=False,
                         allow_overnight=True, max_hold_bars=10)
    trade = _run(bars, 0, spec=spec, product=Product.DELIVERY)
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_price == pytest.approx(91.5)      # day-3 open, honest
    assert pd.Timestamp(trade.close_date).normalize() \
        == pd.Timestamp("2024-03-06", tz="UTC")


# ------------------------------------------------ partials, costs, accounting

def test_partial_on_the_last_manageable_bar_books_and_squares_off():
    closes = [100, 100, 106]
    highs = [100.5, 100.5, 106.5]
    bars = _session("2024-03-04", closes, highs=highs)
    bars["stop_level"] = 95.0
    bars["t1"] = 105.0
    spec = ExecutionSpec(stop_kind="column", stop_col="stop_level",
                         hard_stop_pct=None, target_kind="column",
                         target_col="t1", partial_fraction=0.5)
    trade = _run(bars, 1, spec=spec)
    assert trade.partial and trade.partial_price == pytest.approx(105.0)
    assert trade.exit_reason == "session_squareoff"
    qty = 50_000.0 / 100.0
    expected = 0.5 * qty * 5.0 + 0.5 * qty * 6.0        # half@105, half@106
    assert trade.profit_abs == pytest.approx(expected)


def test_partial_costs_are_charged_per_leg_with_real_cost_model():
    closes = [100, 100, 106, 100]
    highs = [100.5, 100.5, 106.5, 100.5]
    lows = [99.5, 99.5, 103, 99.9]
    bars = _session("2024-03-04", closes, highs=highs, lows=lows)
    bars["stop_level"] = 95.0
    bars["t1"] = 105.0
    spec = ExecutionSpec(stop_kind="column", stop_col="stop_level",
                         hard_stop_pct=None, target_kind="column",
                         target_col="t1", partial_fraction=0.5)
    model = NseEquityCostModel()
    trade = _run(bars, 1, spec=spec, costs=model)
    qty = 50_000.0 / 100.0
    buy = model.side_cost(price=100.0, quantity=qty, is_buy=True,
                          product=Product.INTRADAY)
    sell1 = model.side_cost(price=105.0, quantity=qty * 0.5, is_buy=False,
                            product=Product.INTRADAY)
    sell2 = model.side_cost(price=trade.exit_price, quantity=qty * 0.5,
                            is_buy=False, product=Product.INTRADAY)
    assert trade.cost_ratio == pytest.approx((buy + sell1 + sell2) / 50_000.0)


def test_extreme_slippage_is_reflected_linearly_in_net():
    bars = _with_stop(_session("2024-03-04", [100, 101, 102]), 90.0)
    brutal = NseEquityCostModel(NseCostParams(slippage_pct=0.01))  # 100 bps
    trade = _run(bars, 0, costs=brutal)
    assert trade.cost_ratio > 0.02                # ~2% round trip
    assert trade.profit_ratio == pytest.approx(
        trade.gross_ratio - trade.cost_ratio)


# ---------------------------------------------- strategy-specific pathology

def test_supertrend_never_signals_on_a_monotonic_trend_from_series_start():
    """The state initializes bullish; without a genuine bearish->bullish flip
    there must be NO entry (guards against warmup-boundary phantom flips)."""
    n = 60
    closes = np.linspace(100, 130, n)
    frame = _session("2024-03-04", closes)
    strat = SupertrendContinuation()
    assert not strat.entry_signal(strat.prepare(frame)).any()


def test_insidebar_pattern_freshness_does_not_leak_across_sessions():
    hist_days = pd.bdate_range("2024-01-01", periods=7)
    hist = _concat(*[_session(d.date(), [100.0] * 25) for d in hist_days])
    # pattern forms on the day's LAST two bars; the break happens next morning
    d1 = _session("2024-02-12", [100.0, 100.1, 100.05],
                  highs=[102.0, 100.6, 100.4], lows=[98.0, 99.8, 99.9])
    d2 = _session("2024-02-13", [103.0, 103.5], highs=[103.2, 103.8],
                  lows=[100.4, 103.0])
    strat = InsideBarBreakout()
    sig = strat.entry_signal(strat.prepare(_concat(hist, d1, d2)))
    assert not sig.iloc[-2:].any()                # next-day break: no signal


def test_nr7_gate_after_a_special_short_session_fires_KNOWN_LIMITATION():
    """A Muhurat-like 1-hour session is almost always the 7-day range
    minimum, so the NEXT session's break flags NR7. PINNED as current
    behaviour; classified Minor in STRATEGY_AUDIT_REPORT (the session-type
    feature, when built, is the designed fix)."""
    days = pd.bdate_range("2024-01-01", periods=10)
    wide = _concat(*[
        _session(d.date(), [100, 101, 100.5], highs=[103, 103.5, 103],
                 lows=[97, 98, 97.5]) for d in days])
    muhurat = _session("2024-01-16", [100.1, 100.2], start="18:15",
                       highs=[100.4, 100.4], lows=[100.0, 100.1])
    nxt = _session("2024-01-17", [100.3, 100.6, 100.8],
                   highs=[100.4, 100.7, 100.9], lows=[100.1, 100.2, 100.5],
                   opens=[100.2, 100.3, 100.65])
    strat = Nr7Intraday()
    sig = strat.entry_signal(strat.prepare(_concat(wide, muhurat, nxt)))
    assert sig.any()                              # current (limited) behaviour


def test_gapgo_after_special_session_measures_gap_vs_its_close():
    days = pd.bdate_range("2024-01-01", periods=25)
    hist = _concat(*[_session(d.date(), [100.0] * 25) for d in days])
    muhurat = _session("2024-02-09", [100.0, 100.2], start="18:15")
    gap = _session("2024-02-12", [103.5, 104.6, 105.0],
                   highs=[104.0, 104.8, 105.2], lows=[103.0, 103.4, 104.6],
                   opens=[103.0, 103.4, 104.7])
    strat = GapAndGo()
    sig = strat.entry_signal(strat.prepare(_concat(hist, muhurat, gap)))
    assert sig.sum() == 1                         # vs the last REAL close


def test_prior_day_levels_skip_market_holidays_not_sessions():
    """A holiday between sessions must not blank prior-day levels: the PRIOR
    TRADED session is the reference."""
    from algo.core.indicators import prior_session_ohlc
    fri = _session("2024-03-08", [100, 102], highs=[103, 104], lows=[99, 100])
    tue = _session("2024-03-12", [103, 104])      # Mon 2024-03-11 skipped
    _, h, l, _ = prior_session_ohlc(_concat(fri, tue))
    assert h.iloc[2] == pytest.approx(104.0)
    assert l.iloc[2] == pytest.approx(99.0)


# ------------------------------------------------- statistics spot-audit

def test_summarize_hand_checked_on_a_tiny_trade_set():
    trades = pd.DataFrame({
        "pair": ["A", "A", "A"],
        "open_date": pd.to_datetime(["2024-03-04 04:00", "2024-03-05 04:00",
                                     "2024-03-06 04:00"], utc=True),
        "close_date": pd.to_datetime(["2024-03-04 09:45", "2024-03-05 09:45",
                                      "2024-03-06 09:45"], utc=True),
        "profit_ratio": [0.01, -0.02, 0.005],
        "profit_abs": [500.0, -1000.0, 250.0],
        "stake_amount": [50_000.0] * 3,
        "trade_duration": [345.0] * 3,
        "exit_reason": ["target", "stop_loss", "session_squareoff"],
        "enter_tag": [""] * 3, "stop_distance_pct": [0.05] * 3,
    })
    s = metrics.summarize(trades, 50_000.0)
    assert s["trades"] == 3
    assert s["net_profit_abs"] == pytest.approx(-250.0)
    assert s["win_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert s["profit_factor"] == pytest.approx(750.0 / 1000.0)
    # equity: 50500 -> 49500 -> 49750; peak 50500 -> trough 49500
    assert s["max_drawdown_abs"] == pytest.approx(1000.0)
    # summarize rounds expectancy to 6 decimals for reporting
    assert s["expectancy"] == pytest.approx((0.01 - 0.02 + 0.005) / 3,
                                            abs=1e-6)
