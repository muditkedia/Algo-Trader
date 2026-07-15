"""Universe-filter framework: generic filters + pipeline attribution."""

import numpy as np
import pandas as pd

from algo.universe.base import ColumnRangeFilter, MembershipFilter
from algo.universe.universe import Universe


def _frame():
    return pd.DataFrame(
        {"adv": [1e7, 5e5, 2e6, np.nan], "price": [100.0, 5.0, 3000.0, 50.0]},
        index=["A", "B", "C", "D"])


def test_column_range_filter_min():
    mask = ColumnRangeFilter("adv", min_value=1e6).mask(_frame())
    assert mask.tolist() == [True, False, True, False]  # NaN (D) fails


def test_column_range_filter_keep_missing():
    mask = ColumnRangeFilter("adv", min_value=1e6, keep_missing=True).mask(_frame())
    assert mask["D"]  # NaN kept when explicitly allowed


def test_membership_filter_include_exclude():
    frame = _frame()
    incl = MembershipFilter(["A", "C"]).mask(frame)
    assert incl.tolist() == [True, False, True, False]
    excl = MembershipFilter(["A"], exclude=True).mask(frame)
    assert excl.tolist() == [False, True, True, True]


def test_universe_pipeline_attribution():
    uni = Universe([
        ColumnRangeFilter("adv", min_value=1e6, name="liquidity"),
        ColumnRangeFilter("price", max_value=2000, name="price_cap"),
    ])
    result = uni.apply(_frame())
    assert result.kept == ["A"]
    assert result.dropped == {"B": "liquidity", "D": "liquidity",
                              "C": "price_cap"}
    assert result.per_filter_drops == {"liquidity": 2, "price_cap": 1}
    assert result.n_in == 4 and result.n_kept == 1
