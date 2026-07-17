"""Batch-1 strategies (Phase 9): signal correctness on crafted deterministic
frames, the pre-registered horizon contract, and the weekly-screen lookahead
proof. The cross-strategy suite in test_strategy_library (flat-market silence,
unprepared-frame guard, metadata contract) covers these automatically via
discovery - that automatic coverage is itself the Part-G integration check."""

import numpy as np
import pandas as pd
import pytest

from algo.strategies.library import (
    ALL_STRATEGIES, Donchian55Breakout, EarningsGapContinuation,
    ElderTripleScreen, High52WeekProximity, HighVolumePremium,
    TimeSeriesMomentum, WeeklySqueezeBreakout, WyckoffSpring,
)

BATCH1 = {"tsmom_daily", "hi52_daily", "egap_daily", "donchian55_daily",
          "wyckoff_spring_daily", "squeeze_daily", "hvol_daily",
          "triple_screen_daily"}


def _dates(n, end="2026-06-30"):
    return pd.bdate_range(end=end, periods=n, tz="UTC")


def _frame(closes, highs=None, lows=None, volumes=None, opens=None):
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    return pd.DataFrame({
        "date": _dates(n),
        "open": np.asarray(opens, float) if opens is not None
        else np.concatenate(([closes[0]], closes[:-1])),
        "high": np.asarray(highs, float) if highs is not None else closes + 0.2,
        "low": np.asarray(lows, float) if lows is not None else closes - 0.2,
        "close": closes,
        "volume": np.asarray(volumes, float) if volumes is not None
        else np.full(n, 1000.0),
    })


def _signals(strategy, frame):
    return strategy.entry_signal(strategy.prepare(frame))


# --------------------------------------------------------------- discovery

def test_batch1_discovered():
    names = {cls.meta.name for cls in ALL_STRATEGIES}
    assert BATCH1 <= names
    assert len(names) >= 14                       # six incumbents + batch 1


def test_preregistered_horizons_match_the_document():
    """The horizons measured must be EXACTLY the pre-registered ones."""
    expected = {
        "tsmom_daily": ((5, 10, 20, 40, 60), 60),
        "hi52_daily": ((10, 20, 40, 60), 60),
        "egap_daily": ((5, 10, 20, 40), 40),
        "donchian55_daily": ((10, 20, 40, 60), 60),
        "wyckoff_spring_daily": ((5, 10, 20, 40), 40),
        "squeeze_daily": ((5, 10, 20, 30), 30),
        "hvol_daily": ((5, 10, 20), 20),
        "triple_screen_daily": ((3, 5, 10, 20), 20),
    }
    by_name = {cls.meta.name: cls.meta for cls in ALL_STRATEGIES}
    for name, (horizons, max_hold) in expected.items():
        assert by_name[name].horizon_bars == horizons, name
        assert by_name[name].max_hold_bars == max_hold, name
        assert by_name[name].timeframe == "1d", name
        assert by_name[name].holding_scope.value == "swing", name


# ------------------------------------------------------------- per-strategy

def test_tsmom_fires_on_the_momentum_sign_flip_only():
    n = 330
    closes = np.full(n, 100.0)
    for i in range(281, n):                       # trend starts at bar 281
        closes[i] = closes[i - 1] * 1.01
    signal = _signals(TimeSeriesMomentum(), _frame(closes))
    fired = np.flatnonzero(signal.to_numpy(bool))
    assert len(fired) == 1                        # one crossing, one signal
    assert fired[0] >= 302                        # after the 21-bar skip lag


def test_hi52_fires_on_reentering_the_band():
    n = 300
    closes = np.full(n, 100.0)
    closes[262:285] = 90.0                        # dip out of the band
    closes[285:] = 100.0                          # recovery re-enters it
    signal = _signals(High52WeekProximity(), _frame(closes))
    fired = np.flatnonzero(signal.to_numpy(bool))
    assert 285 in fired
    assert len(fired) == 1


def test_egap_needs_gap_volume_and_hold():
    n = 40
    closes = np.full(n, 100.0)
    opens = np.concatenate(([100.0], closes[:-1])).copy()
    volumes = np.full(n, 1000.0)
    # bar 30: 4% gap, 5x volume, closes above open -> fires
    opens[30], closes[30], volumes[30] = 104.0, 105.0, 5000.0
    # bar 35: identical gap but FADES (close < open) -> must not fire
    opens[35], closes[35], volumes[35] = 104.0, 100.5, 5000.0
    highs = np.maximum(opens, closes) + 0.2
    lows = np.minimum(opens, closes) - 0.2
    signal = _signals(EarningsGapContinuation(),
                      _frame(closes, highs=highs, lows=lows,
                             volumes=volumes, opens=opens))
    assert list(np.flatnonzero(signal.to_numpy(bool))) == [30]


def test_donchian_fires_on_the_channel_break():
    n = 70
    closes = np.full(n, 100.0)
    closes[65:] = 106.0                           # clears the 55d high
    signal = _signals(Donchian55Breakout(), _frame(closes))
    fired = np.flatnonzero(signal.to_numpy(bool))
    assert list(fired) == [65]                    # edge-triggered once


def test_spring_same_bar_reclaim():
    """The breakdown bar itself closes back above the range low -> fires."""
    n = 50
    closes = np.full(n, 100.0)
    lows = closes - 0.2                            # range low ≈ 99.8
    lows = lows.copy()
    lows[40] = 98.0                                # flush below the range low
    frame = _frame(closes, lows=lows)              # close 100 > 99.8: reclaimed
    strat = WyckoffSpring()
    prepared = strat.prepare(frame)
    range_low = float(prepared["range_low"].iloc[40])
    assert lows[40] < range_low < closes[40]       # crafted correctly
    signal = strat.entry_signal(prepared)
    assert list(np.flatnonzero(signal.to_numpy(bool))) == [40]


def test_spring_delayed_reclaim():
    """Breakdown bar closes BELOW the range low; the reclaim two bars later
    crosses back above it -> fires on the reclaim bar."""
    n = 50
    closes = np.full(n, 100.0)
    lows = (closes - 0.2).copy()
    closes = closes.copy()
    lows[45], closes[45] = 97.5, 99.0              # breakdown day, close below
    lows[46], closes[46] = 98.8, 99.3              # still below 99.8
    lows[47], closes[47] = 99.2, 100.2             # crosses back above -> spring
    highs = np.maximum(closes, 100.0) + 0.2
    frame = _frame(closes, highs=highs, lows=lows)
    strat = WyckoffSpring()
    prepared = strat.prepare(frame)
    signal = strat.entry_signal(prepared)
    assert bool(signal.iloc[47])
    assert not bool(signal.iloc[45]) and not bool(signal.iloc[46])


def test_hvol_fires_on_the_volume_spike():
    n = 60
    volumes = np.full(n, 1000.0)
    volumes[57] = 5000.0
    signal = _signals(HighVolumePremium(),
                      _frame(np.full(n, 100.0), volumes=volumes))
    assert list(np.flatnonzero(signal.to_numpy(bool))) == [57]


def _squeeze_frame(n=150, breakout_at=143):
    """Stable closes inside wide daily ranges -> weekly BB (tight) inside
    weekly KC (wide, ATR-driven); then an upside range break."""
    rng = np.random.default_rng(11)
    closes = 100.0 + rng.normal(0, 0.05, n).cumsum() * 0.1
    highs = closes + 3.0
    lows = closes - 3.0
    closes = closes.copy()
    closes[breakout_at:] = 106.0                  # break above the 10d high
    highs = np.maximum(highs, closes + 0.2)
    return _frame(closes, highs=highs, lows=lows)


def test_squeeze_requires_compression_and_break():
    frame = _squeeze_frame()
    strat = WeeklySqueezeBreakout()
    prepared = strat.prepare(frame)
    assert float(prepared["squeeze_on"].iloc[140]) == 1.0   # compressed
    signal = strat.entry_signal(prepared)
    fired = np.flatnonzero(signal.to_numpy(bool))
    assert list(fired) == [143]

    # same break WITHOUT compression (tiny daily ranges -> BB wider than KC
    # is impossible, so force the opposite: volatile closes, tight ranges)
    n = 150
    rng = np.random.default_rng(7)
    wild = 100.0 * np.exp(rng.normal(0, 0.02, n).cumsum())
    wild[143:] = wild[142] * 1.08
    frame2 = _frame(wild, highs=wild + 0.05, lows=wild - 0.05)
    prepared2 = strat.prepare(frame2)
    signal2 = strat.entry_signal(prepared2)
    compressed_fires = signal2 & (prepared2["squeeze_on"] > 0.5)
    assert int(signal2.sum()) == int(compressed_fires.sum())  # gate holds


def _triple_screen_frame(n=110, dip_at=100):
    closes = 100.0 * 1.003 ** np.arange(n)        # steady weekly uptrend
    closes = closes.copy()
    closes[dip_at] = closes[dip_at - 1] * 0.975   # sharp one-day pullback
    closes[dip_at + 1] = closes[dip_at - 1] * 1.01  # strength returns
    highs = closes + 0.1
    lows = closes - 0.1
    return _frame(closes, highs=highs, lows=lows)


def test_triple_screen_fires_after_pullback_in_uptrend():
    frame = _triple_screen_frame()
    strat = ElderTripleScreen()
    prepared = strat.prepare(frame)
    assert float(prepared["tide"].iloc[-1]) == 1.0
    signal = strat.entry_signal(prepared)
    assert bool(signal.iloc[101])                 # the resume bar fires


# --------------------------------------------- weekly-screen lookahead proof

@pytest.mark.parametrize("cls,frame_fn", [
    (WeeklySqueezeBreakout, _squeeze_frame),
    (ElderTripleScreen, _triple_screen_frame),
])
def test_weekly_screens_have_no_lookahead(cls, frame_fn):
    """THE pre-registered truncation test (L-008 methodology): physically
    deleting future bars must not change any earlier signal, wherever the
    frame is cut - including mid-week, where a partial weekly bucket exists."""
    strat = cls()
    frame = frame_fn()
    full = strat.entry_signal(strat.prepare(frame)).to_numpy(bool)
    for cut in (97, 98, 99, 100, 101, 130, 144):   # assorted mid-week cuts
        truncated = frame.iloc[:cut].reset_index(drop=True)
        head = strat.entry_signal(strat.prepare(truncated)).to_numpy(bool)
        assert (head == full[:cut]).all(), f"lookahead at cut={cut}"
