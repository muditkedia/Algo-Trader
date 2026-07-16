"""Core indicator library - pure pandas, promoted from the archived crypto core.

Provenance and reuse:

* ``crossed_above`` / ``crossed_below`` are verbatim ports from the archived
  ``algo_core/indicators.py`` (the lookahead-safe cross triggers every crypto
  profile used).
* ``wilder_ema`` / ``atr`` / ``adx`` use the SAME Wilder formulas as the
  validation package's regime labeler (``algo.research.validation.regime``),
  which is the battle-tested pandas implementation from the crypto phase.
* The talib-based ``add_core_indicators`` from the archive is NOT carried over:
  TA-Lib is a C dependency that is painful on Windows and adds nothing the
  pandas implementations don't provide. The archived talib version remains in
  ``archive/crypto-freqtrade/`` for reference (decision D-017).

Equity additions (session-scoped, needed by the intraday strategies):
``session_vwap`` and ``opening_range`` group by calendar day (a UTC-stored
NSE session maps 1:1 to a date), so both reset at every session boundary.

All computations use only current and previous rows - no forward references -
keeping every signal lookahead-safe.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# --------------------------------------------------------------- primitives


def ema(series: pd.Series, period: int) -> pd.Series:
    """Standard exponential moving average (span parameterization)."""
    return series.ewm(span=period, adjust=False).mean()


def wilder_ema(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (alpha = 1/period) - basis of RSI/ATR/ADX."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI. Flat series -> 50; all-gain -> 100; all-loss -> 0."""
    delta = close.diff()
    gain = wilder_ema(delta.clip(lower=0.0), period)
    loss = wilder_ema((-delta).clip(lower=0.0), period)
    out = 100.0 - 100.0 / (1.0 + gain / loss.replace(0.0, np.nan))
    out = out.where(loss != 0.0, 100.0)
    out = out.where(~((loss == 0.0) & (gain == 0.0)), 50.0)
    return out


def true_range(frame: pd.DataFrame) -> pd.Series:
    """Wilder true range from high/low/close."""
    prev_close = frame["close"].shift(1)
    return pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - prev_close).abs(),
        (frame["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average true range (Wilder smoothing)."""
    return wilder_ema(true_range(frame), period)


def adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ADX - same formulas as the validation regime labeler."""
    high, low = frame["high"], frame["low"]
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0),
                        index=frame.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0),
                         index=frame.index)
    smoothed_tr = wilder_ema(true_range(frame), period)
    plus_di = 100 * wilder_ema(plus_dm, period) / smoothed_tr
    minus_di = 100 * wilder_ema(minus_dm, period) / smoothed_tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return wilder_ema(dx.fillna(0.0), period)


def bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0):
    """(upper, middle, lower) Bollinger bands."""
    mid = close.rolling(period, min_periods=period).mean()
    std = close.rolling(period, min_periods=period).std(ddof=0)
    return mid + num_std * std, mid, mid - num_std * std


def bollinger_bandwidth(close: pd.Series, period: int = 20,
                        num_std: float = 2.0) -> pd.Series:
    """(upper - lower) / middle - the squeeze measure."""
    upper, mid, lower = bollinger(close, period, num_std)
    return (upper - lower) / mid


def volume_ratio(volume: pd.Series, window: int = 20) -> pd.Series:
    """Volume vs its rolling mean (archive semantics: mean includes the
    current bar, full-window minimum periods)."""
    mean = volume.rolling(window, min_periods=window).mean()
    return volume / mean


# ------------------------------------------------------------ cross triggers
# Verbatim from archive/crypto-freqtrade/strategies/algo_core/indicators.py.


def crossed_above(series: pd.Series, reference: pd.Series) -> pd.Series:
    """True on the candle where ``series`` closes above ``reference``."""
    return (series > reference) & (series.shift(1) <= reference.shift(1))


def crossed_below(series: pd.Series, reference: pd.Series) -> pd.Series:
    """True on the candle where ``series`` closes below ``reference``."""
    return (series < reference) & (series.shift(1) >= reference.shift(1))


# ------------------------------------------------------- session-scoped (NSE)


def _session_key(frame: pd.DataFrame) -> pd.Series:
    """Grouping key: the calendar day of each bar (one NSE session per day)."""
    return frame["date"].dt.normalize()


def session_vwap(frame: pd.DataFrame) -> pd.Series:
    """Volume-weighted average price, resetting at each session boundary."""
    day = _session_key(frame)
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    pv = (typical * frame["volume"]).groupby(day).cumsum()
    vol = frame["volume"].groupby(day).cumsum()
    return pv / vol.replace(0.0, np.nan)


def opening_range(frame: pd.DataFrame, minutes: int = 15):
    """Per-session opening range: (or_high, or_low, after_range).

    A bar is inside the opening range when it STARTS within ``minutes`` of the
    session's first bar. ``or_high``/``or_low`` carry the range's extremes
    forward through the session; ``after_range`` marks bars after the window
    (the only bars allowed to trigger a breakout).
    """
    day = _session_key(frame)
    first = frame.groupby(day)["date"].transform("min")
    elapsed_min = (frame["date"] - first).dt.total_seconds() / 60.0
    in_range = elapsed_min < minutes

    or_high = frame["high"].where(in_range).groupby(day).cummax()
    or_high = or_high.groupby(day).ffill()
    or_low = frame["low"].where(in_range).groupby(day).cummin()
    or_low = or_low.groupby(day).ffill()
    return or_high, or_low, ~in_range
