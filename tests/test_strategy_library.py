"""Strategy library: per-strategy signal correctness on crafted deterministic
frames, negative cases, confidence bounds/components, and metadata contracts."""

import numpy as np
import pandas as pd
import pytest

from algo.strategies.library import (
    ALL_STRATEGIES, Ema200PullbackTrend, Nr7VolatilityContraction,
    OpeningRangeBreakout, PullbackContinuation15m, VolatilityExpansionBreakout1h,
    VwapTrendContinuation,
)
from algo.strategies.library.ema200_daily import Ema200Params
from algo.strategies.library.pullback_15m import PullbackParams


# ------------------------------------------------------------ frame builders

def _frame(dates, closes, highs=None, lows=None, volumes=None, opens=None):
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "date": pd.DatetimeIndex(dates),
        "open": np.asarray(opens, dtype=float) if opens is not None
        else np.concatenate(([closes[0]], closes[:-1])),
        "high": np.asarray(highs, dtype=float) if highs is not None
        else closes + 0.5,
        "low": np.asarray(lows, dtype=float) if lows is not None
        else closes - 0.5,
        "close": closes,
        "volume": np.asarray(volumes, dtype=float) if volumes is not None
        else np.full(len(closes), 100.0),
    })


def _intraday_dates(day: str, bars: int, freq: str = "15min"):
    return pd.date_range(f"{day} 09:15", periods=bars, freq=freq, tz="UTC")


def _daily_dates(n: int, end: str = "2024-03-05"):
    return pd.bdate_range(end=end, periods=n, tz="UTC")


def _flat_frame(dates):
    n = len(dates)
    c = np.full(n, 100.0)
    return _frame(dates, c, highs=c, lows=c, volumes=np.full(n, 100.0))


def _ema_step(prev: float, value: float, period: int) -> float:
    alpha = 2.0 / (period + 1.0)
    return alpha * value + (1 - alpha) * prev


# ---------------------------------------------------------------- ORB (15m)

def _orb_frame():
    """Two sessions; session-2 bar 22 breaks the opening-range high on 3x
    volume. Frame ENDS on the breakout bar."""
    d1 = _intraday_dates("2024-03-04", 25)
    d2 = _intraday_dates("2024-03-05", 23)
    closes, highs, lows, vols = [], [], [], []
    # session 1: quiet
    for i in range(25):
        c = 100.0 + 0.05 * (i % 3)
        closes.append(c); highs.append(c + 0.3); lows.append(c - 0.3)
        vols.append(100.0)
    # session 2 bar 0: opening range with high 105
    closes.append(104.0); highs.append(105.0); lows.append(99.0); vols.append(100.0)
    # session 2 bars 1..21: below the range high
    for i in range(1, 22):
        c = 102.0 + 0.05 * (i % 4)
        closes.append(c); highs.append(c + 0.4); lows.append(c - 0.4)
        vols.append(100.0)
    # session 2 bar 22: breakout on volume
    closes.append(106.0); highs.append(106.5); lows.append(103.9); vols.append(300.0)
    return _frame(list(d1) + list(d2), closes, highs, lows, vols)


def test_orb_fires_on_confirmed_breakout():
    strat = OpeningRangeBreakout()
    prepared = strat.prepare(_orb_frame())
    signal = strat.entry_signal(prepared)
    assert bool(signal.iloc[-1])                     # breakout bar fires
    assert int(signal.sum()) == 1                    # and ONLY that bar
    conf = strat.confidence(prepared)
    assert 0.0 <= conf.score <= 1.0 and conf.reason
    assert set(conf.components) == {"volume_surge", "range_tightness",
                                    "close_strength"}
    assert conf.components["volume_surge"]["score"] > 0.5   # 3x volume


def test_orb_requires_volume_confirmation():
    frame = _orb_frame()
    frame.loc[frame.index[-1], "volume"] = 100.0     # same break, no surge
    strat = OpeningRangeBreakout()
    signal = strat.entry_signal(strat.prepare(frame))
    assert int(signal.sum()) == 0


# --------------------------------------------------------------- NR7 (daily)

def _nr7_frame():
    """40 daily bars; bar 38 is the NR7 day; bar 39 breaks its high on volume."""
    dates = _daily_dates(40)
    closes, highs, lows, vols = [], [], [], []
    for i in range(38):
        c = 100.0 + 0.1 * (i % 5)
        closes.append(c); highs.append(c + 1.0); lows.append(c - 1.0)
        vols.append(100.0)
    closes.append(100.2); highs.append(100.4); lows.append(100.0)  # NR7 day
    vols.append(100.0)
    closes.append(101.5); highs.append(101.8); lows.append(100.1)  # breakout
    vols.append(300.0)
    return _frame(dates, closes, highs, lows, vols)


def test_nr7_fires_on_break_of_narrow_day():
    strat = Nr7VolatilityContraction()
    prepared = strat.prepare(_nr7_frame())
    assert bool(prepared["nr_prev"].iloc[-1])        # yesterday was the NR7 day
    signal = strat.entry_signal(prepared)
    assert bool(signal.iloc[-1]) and int(signal.sum()) == 1
    conf = strat.confidence(prepared)
    assert 0.0 <= conf.score <= 1.0
    assert conf.components["contraction_depth"]["score"] > 0.5  # tight coil


def test_nr7_no_signal_without_contraction():
    frame = _nr7_frame()
    frame.loc[frame.index[38], ["high", "low"]] = [103.0, 98.0]  # wide day
    strat = Nr7VolatilityContraction()
    assert int(strat.entry_signal(strat.prepare(frame)).sum()) == 0


# ------------------------------------------------------------- volexp (1h)

def _volexp_frame():
    """45 hourly bars: long tight squeeze, then a close through the upper band."""
    dates = pd.date_range("2024-03-04 04:00", periods=45, freq="1h", tz="UTC")
    closes, highs, lows, vols = [], [], [], []
    for i in range(44):
        c = 100.0 + 0.03 * ((-1) ** i)               # +-3 paise wiggle
        closes.append(c); highs.append(c + 0.05); lows.append(c - 0.05)
        vols.append(100.0)
    closes.append(101.5); highs.append(101.8); lows.append(100.0)
    vols.append(250.0)
    return _frame(dates, closes, highs, lows, vols)


def test_volexp_fires_on_squeeze_release():
    strat = VolatilityExpansionBreakout1h()
    prepared = strat.prepare(_volexp_frame())
    signal = strat.entry_signal(prepared)
    assert bool(signal.iloc[-1]) and int(signal.sum()) == 1
    conf = strat.confidence(prepared)
    assert 0.0 <= conf.score <= 1.0
    assert conf.components["squeeze_tightness"]["score"] > 0.5


def test_volexp_no_signal_without_squeeze():
    frame = _volexp_frame()
    rng = np.random.default_rng(1)
    noisy = 100.0 + np.cumsum(rng.normal(0, 1.2, len(frame)))  # wide bands
    frame["close"] = noisy
    frame["high"] = noisy + 1.5
    frame["low"] = noisy - 1.5
    strat = VolatilityExpansionBreakout1h()
    prepared = strat.prepare(frame)
    prior_width = prepared["bb_width"].shift(1).rolling(3).max()
    fired = strat.entry_signal(prepared)
    # any fire would require a genuine prior squeeze - verify none existed
    assert not bool((fired & (prior_width > strat.settings.bandwidth_max))
                    .any())


# --------------------------------------------------------------- VWAP (15m)

def _vwap_frame():
    """Session 1 quiet (volume warmup); session 2 buyer-controlled, one dip
    through VWAP, reclaimed on the final bar."""
    d1 = _intraday_dates("2024-03-04", 25)
    closes = [100.0 + 0.02 * (i % 3) for i in range(25)]
    # session 2: rising, above VWAP
    s2 = [100.0 + 1.0 * i for i in range(10)]        # 100..109
    s2.append(103.0)                                  # dip below running VWAP
    s2.append(106.0)                                  # reclaim (fire)
    d2 = _intraday_dates("2024-03-05", len(s2))
    all_closes = closes + s2
    return _frame(list(d1) + list(d2), all_closes)


def test_vwap_fires_on_reclaim():
    strat = VwapTrendContinuation()
    prepared = strat.prepare(_vwap_frame())
    last = prepared.iloc[-1]
    assert last["above_share"] >= strat.settings.above_share_min
    assert prepared["close"].iloc[-2] < prepared["vwap"].iloc[-2]  # dip real
    signal = strat.entry_signal(prepared)
    assert bool(signal.iloc[-1])
    conf = strat.confidence(prepared)
    assert 0.0 <= conf.score <= 1.0
    assert conf.components["session_control"]["score"] > 0.5


def test_vwap_no_signal_on_seller_controlled_session():
    frame = _vwap_frame()
    # invert session 2: falling prices -> below VWAP most of the session
    n2 = 12
    fall = [109.0 - 1.0 * i for i in range(n2 - 2)] + [104.0, 105.5]
    frame.loc[frame.index[-n2:], "close"] = fall
    frame.loc[frame.index[-n2:], "high"] = np.asarray(fall) + 0.5
    frame.loc[frame.index[-n2:], "low"] = np.asarray(fall) - 0.5
    strat = VwapTrendContinuation()
    prepared = strat.prepare(frame)
    assert not bool(strat.entry_signal(prepared).iloc[-1])


# ---------------------------------------------------------- pullback (15m)

def _pullback_frame(params: PullbackParams):
    """Constructive: steady uptrend, then a dip below the (incrementally
    tracked) fast EMA, then a reclaim bar. Fires on the LAST bar."""
    n_trend = 110
    closes = [100.0]
    for _ in range(n_trend - 1):
        closes.append(closes[-1] * 1.003)
    ema_fast = closes[0]
    for c in closes[1:]:
        ema_fast = _ema_step(ema_fast, c, params.ema_fast)
    # dip bar: close (and low) below the current fast EMA
    dip_close = ema_fast * 0.995
    closes.append(dip_close)
    lows = [c - 0.5 for c in closes]
    lows[-1] = dip_close - 1.0
    ema_fast = _ema_step(ema_fast, dip_close, params.ema_fast)
    # reclaim bar: close back above the fast EMA
    reclaim = ema_fast * 1.02
    closes.append(reclaim)
    lows.append(reclaim - 0.5)
    dates = _intraday_dates("2024-03-04", 25)
    extra_days = ["2024-03-05", "2024-03-06", "2024-03-07", "2024-03-08"]
    all_dates = list(dates)
    for day in extra_days:
        all_dates += list(_intraday_dates(day, 25))
    all_dates = all_dates[:len(closes)]
    vols = np.full(len(closes), 100.0)
    vols[-1] = 250.0
    return _frame(all_dates, closes,
                  highs=[c + 0.5 for c in closes], lows=lows, volumes=vols)


def test_pullback_fires_on_reclaim_after_dip():
    strat = PullbackContinuation15m()
    prepared = strat.prepare(_pullback_frame(strat.settings))
    # dip bar really closed below the fast EMA; reclaim closed above
    assert prepared["close"].iloc[-2] < prepared["ema_fast"].iloc[-2]
    assert prepared["close"].iloc[-1] > prepared["ema_fast"].iloc[-1]
    signal = strat.entry_signal(prepared)
    assert bool(signal.iloc[-1])
    conf = strat.confidence(prepared)
    assert 0.0 <= conf.score <= 1.0
    assert conf.components["trend_strength"]["score"] > 0.0


def test_pullback_requires_uptrend():
    strat = PullbackContinuation15m()
    frame = _pullback_frame(strat.settings)
    frame["close"] = frame["close"].iloc[::-1].to_numpy()   # downtrend
    frame["high"] = frame["close"] + 0.5
    frame["low"] = frame["close"] - 0.5
    prepared = strat.prepare(frame)
    fired = strat.entry_signal(prepared)
    uptrend = (prepared["ema_fast"] > prepared["ema_slow"]) \
        & (prepared["close"] > prepared["ema_slow"])
    assert not bool((fired & ~uptrend).any())        # never fires against trend


# ------------------------------------------------------------ ema200 (daily)

def _ema200_frame(params: Ema200Params):
    """Constructive daily uptrend, pullback into the trend-EMA zone, resume."""
    closes = [100.0]
    for _ in range(54):
        closes.append(closes[-1] * 1.004)
    ema_trend = closes[0]
    ema_fast = closes[0]
    for c in closes[1:]:
        ema_trend = _ema_step(ema_trend, c, params.ema_trend)
        ema_fast = _ema_step(ema_fast, c, params.ema_fast)
    # two dip bars: lows into the anchor's proximity band, closes below fast EMA
    for _ in range(2):
        dip_close = ema_fast * 0.985
        closes.append(dip_close)
        ema_trend = _ema_step(ema_trend, dip_close, params.ema_trend)
        ema_fast = _ema_step(ema_fast, dip_close, params.ema_fast)
    # resume bar: close back above the fast EMA
    closes.append(ema_fast * 1.02)
    lows = [c - 0.3 for c in closes]
    lows[-3] = ema_trend * 1.004                     # touch near the anchor
    lows[-2] = ema_trend * 1.002
    dates = _daily_dates(len(closes))
    return _frame(dates, closes, highs=[c + 0.3 for c in closes], lows=lows)


def test_ema200_fires_on_resumption(reduced=True):
    params = Ema200Params(ema_trend=30, ema_fast=10, slope_lookback=5,
                          touch_lookback=5, proximity_band=0.03)
    strat = Ema200PullbackTrend(settings=params)
    prepared = strat.prepare(_ema200_frame(params))
    last = prepared.iloc[-1]
    assert last["close"] > last["ema_trend"] and last["trend_slope"] > 0
    signal = strat.entry_signal(prepared)
    assert bool(signal.iloc[-1])
    conf = strat.confidence(prepared)
    assert 0.0 <= conf.score <= 1.0
    assert set(conf.components) == {"anchor_slope", "pullback_proximity",
                                    "rsi_recovery", "volume"}


def test_ema200_insufficient_history_is_silent():
    strat = Ema200PullbackTrend()                    # default 200-day params
    short = _ema200_frame(Ema200Params(ema_trend=30, ema_fast=10,
                                       slope_lookback=5))
    signal = strat.entry_signal(strat.prepare(short))   # 58 bars << 230
    assert int(signal.sum()) == 0


# ------------------------------------------------------- cross-strategy suite

@pytest.mark.parametrize("cls", ALL_STRATEGIES, ids=lambda c: c.meta.name)
def test_no_signal_on_flat_market(cls):
    strat = cls()
    tf = strat.meta.timeframe
    if tf == "1d":
        dates = _daily_dates(260)
    elif tf == "1h":
        dates = pd.date_range("2024-01-01", periods=300, freq="1h", tz="UTC")
    else:
        dates = []
        for day in pd.bdate_range("2024-03-04", periods=12):
            dates += list(_intraday_dates(day.date().isoformat(), 25))
    frame = _flat_frame(pd.DatetimeIndex(dates))
    signal = strat.entry_signal(strat.prepare(frame))
    assert int(signal.sum()) == 0


@pytest.mark.parametrize("cls", ALL_STRATEGIES, ids=lambda c: c.meta.name)
def test_unprepared_frame_never_raises(cls):
    strat = cls()
    raw = _flat_frame(_daily_dates(300))             # no indicator columns
    signal = strat.entry_signal(raw)
    assert int(signal.sum()) == 0                    # guard, not crash
    assert strat.confidence(raw).score == 0.0


@pytest.mark.parametrize("cls", ALL_STRATEGIES, ids=lambda c: c.meta.name)
def test_metadata_contract(cls):
    meta = cls.meta
    assert meta.enabled and meta.timeframe in ("15m", "1h", "1d")
    assert meta.supported_regimes and meta.hypothesis
    assert meta.expected_behaviour and meta.known_failure_modes
    assert meta.required_columns
    # A candidate must pre-register the horizon its edge is claimed at.
    assert meta.horizon_bars and min(meta.horizon_bars) >= 1
    assert meta.max_hold_bars >= max(meta.horizon_bars)
    strat = cls()
    assert strat.min_history() >= 2


def test_strategy_names_unique():
    # Uniqueness is now enforced at import (the registry raises on a duplicate
    # name), so this asserts the invariant rather than a strategy count that a
    # new candidate would have to remember to bump.
    names = [cls.meta.name for cls in ALL_STRATEGIES]
    assert len(names) == len(set(names)) >= 6
