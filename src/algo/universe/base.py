"""Universe-filter framework - the ``Filter`` contract and generic filters.

A filter takes a universe frame (one row per symbol, columns = metrics such as
average traded value, price, ATR%, relative volume, index membership) and
returns a boolean mask of the symbols that pass. Filters are independent and
composable; the ``Universe`` orchestrator chains them.

Phase 1 ships the framework plus two GENERIC, fully-configurable filters
(``ColumnRangeFilter``, ``MembershipFilter``). These encode no market rules -
the column, thresholds, and symbol sets are all supplied by config. Concrete
NSE eligibility rules (min traded value in rupees, F&O-only, sector lists) are
just configured instances of these, built in a later phase.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Optional

import pandas as pd


class Filter(ABC):
    """One universe filter. Subclasses implement ``mask``."""

    #: Human-readable identifier, recorded as the drop reason.
    name: str = "filter"

    @abstractmethod
    def mask(self, frame: pd.DataFrame) -> pd.Series:
        """Boolean Series indexed like ``frame``: True = symbol passes."""

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"<{type(self).__name__} name={self.name!r}>"


class ColumnRangeFilter(Filter):
    """Keep symbols whose ``column`` is within [min_value, max_value].

    Both bounds optional. Missing values (NaN) fail by default (a symbol we
    cannot measure is not eligible) unless ``keep_missing=True``.
    """

    def __init__(self, column: str, *, min_value: Optional[float] = None,
                 max_value: Optional[float] = None, keep_missing: bool = False,
                 name: Optional[str] = None) -> None:
        self.column = column
        self.min_value = min_value
        self.max_value = max_value
        self.keep_missing = keep_missing
        self.name = name or f"range:{column}"

    def mask(self, frame: pd.DataFrame) -> pd.Series:
        if self.column not in frame.columns:
            raise KeyError(f"{self.name}: column '{self.column}' not in frame")
        series = frame[self.column]
        ok = pd.Series(True, index=frame.index)
        if self.min_value is not None:
            ok &= series >= self.min_value
        if self.max_value is not None:
            ok &= series <= self.max_value
        missing = series.isna()
        ok = ok & ~missing if not self.keep_missing else ok | missing
        return ok.fillna(self.keep_missing).astype(bool)


class MembershipFilter(Filter):
    """Keep (whitelist) or drop (blacklist) symbols in a supplied set.

    The set is provided by the caller - index constituents, an F&O list, a
    manual blacklist - so this filter is market-agnostic.
    """

    def __init__(self, symbols: Iterable[str], *, exclude: bool = False,
                 name: Optional[str] = None) -> None:
        self.symbols = frozenset(symbols)
        self.exclude = exclude
        self.name = name or ("blacklist" if exclude else "whitelist")

    def mask(self, frame: pd.DataFrame) -> pd.Series:
        member = frame.index.isin(self.symbols)
        keep = ~member if self.exclude else member
        return pd.Series(keep, index=frame.index)
