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
  git history (``archive/crypto-freqtrade/``, since removed) - decision D-017.

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


def roc(series: pd.Series, period: int = 20) -> pd.Series:
    """Rate of change as a decimal fraction (``0.015`` = +1.5%)."""
    return series.pct_change(periods=period, fill_method=None)


def macd_histogram(close: pd.Series, fast: int = 12, slow: int = 26,
                   signal: int = 9) -> pd.Series:
    """Standard MACD histogram using the shared EMA implementation."""
    line = ema(close, fast) - ema(close, slow)
    return line - ema(line, signal)


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


def directional_movement(frame: pd.DataFrame, period: int = 14
                         ) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Wilder ``(+DI, -DI, ADX)`` from one shared calculation.

    ``adx`` remains the stable public convenience wrapper.  Strategies that
    genuinely need directional confirmation use this richer result instead of
    reimplementing the same true-range and Wilder smoothing arithmetic.
    """
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
    return plus_di, minus_di, wilder_ema(dx.fillna(0.0), period)


def adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ADX - same formulas as the validation regime labeler."""
    return directional_movement(frame, period)[2]


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


def slot_relative_volume(frame: pd.DataFrame, sessions: int = 10) -> pd.Series:
    """Volume versus the same intraday candle slot in prior sessions.

    The current session is excluded with ``shift(1)`` before the rolling mean,
    making the result causal and suitable for both replay and live evaluation.
    A full ``sessions``-day history is required for a value.
    """
    dates = pd.to_datetime(frame["date"])
    local = dates.dt.tz_convert("Asia/Kolkata") if dates.dt.tz is not None \
        else dates
    slots = local.dt.hour * 60 + local.dt.minute
    day = local.dt.normalize()
    keyed = pd.DataFrame({"day": day, "slot": slots,
                          "volume": frame["volume"].astype(float)},
                         index=frame.index)
    baseline = keyed.groupby("slot", sort=False)["volume"].transform(
        lambda values: values.shift(1).rolling(
            sessions, min_periods=sessions).mean())
    return keyed["volume"] / baseline.replace(0.0, np.nan)


def rolling_linear_channel(close: pd.Series, lookback: int = 20,
                           std_mult: float = 2.0) -> pd.DataFrame:
    """Prior-only rolling OLS line, normalized slope, R² and envelopes.

    Row ``i`` fits rows ``[i-lookback, i)`` and projects that fit one bar to
    ``i``.  The evaluation candle is therefore never part of the channel it
    is testing.  Callers that require intraday-only geometry apply this helper
    independently to each session.
    """
    values = close.astype(float).to_numpy()
    n = len(values)
    baseline = np.full(n, np.nan)
    slope_pct = np.full(n, np.nan)
    r2 = np.full(n, np.nan)
    resid_std = np.full(n, np.nan)
    x = np.arange(1.0, lookback + 1.0)
    x_centered = x - x.mean()
    x_ss = float(np.dot(x_centered, x_centered))
    for i in range(lookback, n):
        y = values[i - lookback:i]
        if not np.isfinite(y).all():
            continue
        y_mean = float(y.mean())
        beta = float(np.dot(x_centered, y - y_mean) / x_ss)
        intercept = y_mean - beta * float(x.mean())
        fitted = intercept + beta * x
        residuals = y - fitted
        ss_total = float(np.dot(y - y_mean, y - y_mean))
        ss_resid = float(np.dot(residuals, residuals))
        line = intercept + beta * (lookback + 1.0)
        sigma = float(np.sqrt(ss_resid / lookback))
        baseline[i] = line
        slope_pct[i] = beta / line if line != 0.0 else np.nan
        r2[i] = max(0.0, 1.0 - ss_resid / ss_total) \
            if ss_total > 0.0 else 0.0
        resid_std[i] = sigma
    return pd.DataFrame({
        "baseline": baseline,
        "slope_pct": slope_pct,
        "r2": r2,
        "resid_std": resid_std,
        "upper": baseline + std_mult * resid_std,
        "lower": baseline - std_mult * resid_std,
    }, index=close.index)


# ------------------------------------------------------------ cross triggers
# Verbatim from the archived crypto core's indicators.py (see git history).


def crossed_above(series: pd.Series, reference: pd.Series) -> pd.Series:
    """True on the candle where ``series`` closes above ``reference``."""
    return (series > reference) & (series.shift(1) <= reference.shift(1))


def crossed_below(series: pd.Series, reference: pd.Series) -> pd.Series:
    """True on the candle where ``series`` closes below ``reference``."""
    return (series < reference) & (series.shift(1) >= reference.shift(1))


# ------------------------------------------------------------- weekly frame


def weekly_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate daily bars into calendar weeks (label = the week's Friday).

    Weeks with no sessions (exchange holidays spanning a week) are dropped.
    Used by the weekly-screen strategies; the lookahead-safe join back to the
    daily frame is ``weekly_asof``.
    """
    weekly = frame.set_index("date").resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"})
    return weekly.dropna(subset=["close"])


def weekly_asof(dates: pd.Series, weekly_values: pd.Series) -> pd.Series:
    """Join a weekly series to daily bars without lookahead.

    Each daily bar receives the value of the most recent week whose label
    (its Friday) falls ON OR BEFORE the bar's calendar day - so a Friday bar
    may use the week completing at its own close (all inputs known at that
    close), while Monday-Thursday bars see only strictly earlier weeks. A
    mid-week PARTIAL bucket is never joined: its label (the upcoming Friday)
    lies in the future of every bar inside it. Bars before the first complete
    week get NaN. Booleans should be passed as floats (ffill of NaN-holed
    bools loses dtype).
    """
    aligned = weekly_values.reindex(dates.dt.normalize(), method="ffill")
    aligned.index = dates.index
    return aligned


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


def central_pivot_range(frame: pd.DataFrame):
    """Central Pivot Range from the PRIOR session, per bar: (pivot, top, bottom).

    Standard CPR from the prior day's completed OHLC:
        pivot = (H + L + C) / 3 ; BC = (H + L) / 2 ; TC = 2*pivot - BC
    top/bottom are max/min(TC, BC). Each bar receives the PRIOR session's levels
    (shifted by one session), so the values are known at the current session's
    open - causal, no lookahead. The first session (no prior day) gets NaN.
    """
    day = _session_key(frame)
    agg = frame.groupby(day).agg(h=("high", "max"), l=("low", "min"),
                                 c=("close", "last"))
    pivot = (agg["h"] + agg["l"] + agg["c"]) / 3.0
    bc = (agg["h"] + agg["l"]) / 2.0
    tc = 2.0 * pivot - bc
    top = pd.concat([tc, bc], axis=1).max(axis=1)
    bot = pd.concat([tc, bc], axis=1).min(axis=1)
    # each session sees the PRIOR session's levels
    prior_pivot = pivot.shift(1)
    prior_top = top.shift(1)
    prior_bot = bot.shift(1)
    return (day.map(prior_pivot), day.map(prior_top), day.map(prior_bot))


def floor_pivot_levels(frame: pd.DataFrame):
    """Classic floor-pivot levels from the PRIOR session, per bar:
    (pivot, r1, r2).

    pivot = (H + L + C) / 3 ; R1 = 2*pivot - L ; R2 = pivot + (H - L), all from
    the prior session's completed OHLC (shifted by one session - causal, no
    lookahead; the first session gets NaN). The widely-used intraday resistance
    ladder that CPR-style strategies target.
    """
    day = _session_key(frame)
    agg = frame.groupby(day).agg(h=("high", "max"), l=("low", "min"),
                                 c=("close", "last"))
    pivot = (agg["h"] + agg["l"] + agg["c"]) / 3.0
    r1 = 2.0 * pivot - agg["l"]
    r2 = pivot + (agg["h"] - agg["l"])
    return (day.map(pivot.shift(1)), day.map(r1.shift(1)),
            day.map(r2.shift(1)))


def floor_pivot_supports(frame: pd.DataFrame):
    """Classic floor-pivot SUPPORT levels from the PRIOR session, per bar:
    (s1, s2). S1 = 2*pivot - H ; S2 = pivot - (H - L). Companion to
    ``floor_pivot_levels`` (additive - that function's signature is frozen by
    its existing callers). Causal: prior session only; first session NaN."""
    day = _session_key(frame)
    agg = frame.groupby(day).agg(h=("high", "max"), l=("low", "min"),
                                 c=("close", "last"))
    pivot = (agg["h"] + agg["l"] + agg["c"]) / 3.0
    s1 = 2.0 * pivot - agg["h"]
    s2 = pivot - (agg["h"] - agg["l"])
    return (day.map(s1.shift(1)), day.map(s2.shift(1)))


def prior_session_ohlc(frame: pd.DataFrame):
    """The PRIOR session's (open, high, low, close) carried onto each bar.

    Causal by one-session shift (the ``central_pivot_range`` pattern); the
    first session gets NaN. Shared by gap and prior-day-level strategies."""
    day = _session_key(frame)
    agg = frame.groupby(day).agg(o=("open", "first"), h=("high", "max"),
                                 l=("low", "min"), c=("close", "last"))
    return (day.map(agg["o"].shift(1)), day.map(agg["h"].shift(1)),
            day.map(agg["l"].shift(1)), day.map(agg["c"].shift(1)))


def supertrend(frame: pd.DataFrame, period: int = 10,
               multiplier: float = 3.0):
    """Supertrend indicator: (line, direction). Canonical formulation
    (Olivier Seban; the TradingView/India-standard rules) with Wilder ATR:

    basic upper/lower = hl2 +/- multiplier x ATR(period); the final bands
    ratchet (upper only falls unless price closed above it; lower only rises
    unless price closed below it); direction flips to +1 when close crosses
    above the final upper band and to -1 when close crosses below the final
    lower band; ``line`` is the active band (lower in an uptrend - a rising
    stop under price; upper in a downtrend).

    Iterative by definition (each band depends on the prior final band), so
    this runs a plain loop; deterministic, NaN until ATR warms up.
    """
    hl2 = (frame["high"] + frame["low"]) / 2.0
    band = multiplier * atr(frame, period)
    upper_basic = (hl2 + band).to_numpy(float)
    lower_basic = (hl2 - band).to_numpy(float)
    close = frame["close"].to_numpy(float)
    n = len(frame)
    line = np.full(n, np.nan)
    direction = np.zeros(n)
    upper = np.nan
    lower = np.nan
    trend = 1
    for i in range(n):
        if np.isnan(upper_basic[i]):
            continue
        if np.isnan(upper):                      # first computable bar
            upper, lower = upper_basic[i], lower_basic[i]
        else:
            upper = (upper_basic[i]
                     if upper_basic[i] < upper or close[i - 1] > upper
                     else upper)
            lower = (lower_basic[i]
                     if lower_basic[i] > lower or close[i - 1] < lower
                     else lower)
        if trend == 1 and close[i] < lower:
            trend = -1
        elif trend == -1 and close[i] > upper:
            trend = 1
        direction[i] = trend
        line[i] = lower if trend == 1 else upper
    return (pd.Series(line, index=frame.index),
            pd.Series(direction, index=frame.index))
