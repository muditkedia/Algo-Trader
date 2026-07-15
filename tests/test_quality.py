"""Data-quality gates: errors block, warnings admit."""

import numpy as np

from algo.data.quality import check_ohlcv


def _good(synthetic):
    return synthetic.fetch_ohlcv("AAA", "1d", "2024-01-01", "2024-02-01")


def test_good_frame_passes(synthetic, calendar):
    report = check_ohlcv(_good(synthetic), "AAA", "1d", calendar)
    assert report.ok and not report.errors


def test_empty_is_error():
    import pandas as pd
    report = check_ohlcv(pd.DataFrame(), "AAA", "1d")
    assert not report.ok and any(i.code == "empty" for i in report.errors)


def test_high_lt_low_is_error(synthetic):
    df = _good(synthetic).copy()
    df.loc[0, "high"] = df.loc[0, "low"] - 1
    report = check_ohlcv(df, "AAA", "1d")
    assert not report.ok
    assert any(i.code == "high_lt_low" for i in report.errors)


def test_nan_is_error(synthetic):
    df = _good(synthetic).copy()
    df.loc[0, "close"] = np.nan
    assert not check_ohlcv(df, "AAA", "1d").ok


def test_nonpositive_price_is_error(synthetic):
    df = _good(synthetic).copy()
    df.loc[0, ["open", "high", "low", "close"]] = [-1.0, -1.0, -2.0, -1.5]
    report = check_ohlcv(df, "AAA", "1d")
    assert any(i.code == "nonpositive_price" for i in report.errors)


def test_duplicates_are_recoverable_warning(synthetic):
    import pandas as pd
    df = _good(synthetic)
    dup = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    report = check_ohlcv(dup, "AAA", "1d")
    assert report.ok  # deduped downstream -> not blocking
    assert any(i.code == "duplicate_timestamps" for i in report.warnings)


def test_session_gap_is_warning(synthetic, calendar):
    df = _good(synthetic).drop(index=5).reset_index(drop=True)
    report = check_ohlcv(df, "AAA", "1d", calendar)
    assert report.ok
    assert any(i.code == "session_gaps" for i in report.warnings)
