"""Strategy library: per-strategy signal correctness on crafted deterministic
frames, negative cases, confidence bounds/components, and metadata contracts."""

import numpy as np
import pandas as pd
import pytest

from algo.strategies.library import (
    ALL_STRATEGIES, OpeningRangeBreakout, PullbackContinuation15m,
    VolatilityExpansionBreakout1h, VwapTrendContinuation,
)
from algo.strategies.library.pullback_15m import PullbackParams
from algo.core.enums import Direction
from strat01_fixtures import strat01_frames


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


def _flat_frame(dates):
    n = len(dates)
    c = np.full(n, 100.0)
    return _frame(dates, c, highs=c, lows=c, volumes=np.full(n, 100.0))


def _ema_step(prev: float, value: float, period: int) -> float:
    alpha = 2.0 / (period + 1.0)
    return alpha * value + (1 - alpha) * prev


# ---------------------------------------------------------------- STRAT-01 ORB (5m)


def test_orb_fires_on_confirmed_breakout():
    frames = strat01_frames("long")
    strat = OpeningRangeBreakout()
    prepared = strat.prepare(frames["RELIANCE"])
    strat.prepare_context({"RELIANCE": prepared},
                          {"NIFTY50": frames["NIFTY50"]})
    signal = strat.entry_signals(prepared)[Direction.LONG]
    assert bool(signal.iloc[-1])                     # breakout bar fires
    assert int(signal.sum()) == 1                    # and ONLY that bar
    conf = strat.confidence_for(prepared, Direction.LONG)
    assert 0.0 <= conf.score <= 1.0 and conf.reason
    assert conf.score >= 0.55
    assert strat.regime_score(prepared, Direction.LONG) >= 0.60


def test_orb_requires_volume_confirmation():
    frames = strat01_frames("long")
    frame = frames["RELIANCE"]
    frame.loc[frame.index[-1], "volume"] = 500_000.0
    strat = OpeningRangeBreakout()
    prepared = strat.prepare(frame)
    strat.prepare_context({"RELIANCE": prepared},
                          {"NIFTY50": frames["NIFTY50"]})
    signal = strat.entry_signals(prepared)[Direction.LONG]
    assert int(signal.sum()) == 0


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


# ------------------------------------------------------- cross-strategy suite


@pytest.mark.parametrize("cls", ALL_STRATEGIES, ids=lambda c: c.meta.name)
def test_no_signal_on_flat_market(cls):
    strat = cls()
    tf = strat.meta.timeframe
    if tf == "1h":
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
    raw = _flat_frame(_intraday_dates("2024-03-04", 25))  # no indicators
    signal = strat.entry_signal(raw)
    assert int(signal.sum()) == 0                    # guard, not crash
    assert strat.confidence(raw).score == 0.0


@pytest.mark.parametrize("cls", ALL_STRATEGIES, ids=lambda c: c.meta.name)
def test_metadata_contract(cls):
    meta = cls.meta
    assert meta.enabled and meta.timeframe in ("5m", "15m", "1h")
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
