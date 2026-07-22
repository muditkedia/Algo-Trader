"""DataJobs - the three scheduling entry points over the ingestion engine.

* ``full_import``      - one-time backfill of an explicit window.
* ``daily_update``     - post-close top-up to the latest session (EOD).
* ``intraday_refresh`` - top-up today's partial bars during the session.

All three are incremental and idempotent (the ingestion engine only fetches the
missing window and the store upserts), so they are safe to run repeatedly - the
core "no duplicate downloads" guarantee holds at the scheduling layer too.
"""

from __future__ import annotations

from typing import List

import pandas as pd

from algo.core.logging import get_logger
from algo.data.ingest import IngestionEngine, IngestionReport

logger = get_logger("schedule.jobs")


class DataJobs:
    def __init__(self, ingestion: IngestionEngine, calendar=None) -> None:
        self.ingestion = ingestion
        self.calendar = calendar

    def full_import(self, symbols: List[str], timeframe: str,
                    start, end) -> IngestionReport:
        logger.info("job full_import: %d symbols %s [%s..%s]",
                    len(symbols), timeframe, start, end)
        return self.ingestion.full_import(symbols, timeframe, start, end)

    def daily_update(self, symbols: List[str], timeframe: str = "1d",
                     as_of=None, start_if_empty=None) -> IngestionReport:
        """Incrementally top up to the latest completed session on/before as_of."""
        as_of = pd.Timestamp(as_of, tz="UTC") if as_of is not None \
            else pd.Timestamp.now(tz="UTC")
        end = self._last_session_end(as_of)
        logger.info("job daily_update: %d symbols %s -> %s",
                    len(symbols), timeframe, end)
        return self.ingestion.incremental_update(
            symbols, timeframe, end=end, start_if_empty=start_if_empty)

    def intraday_refresh(self, symbols: List[str], timeframe: str = "5m",
                         as_of=None, start_if_empty=None) -> IngestionReport:
        """Incrementally top up today's partial bars (end = now/as_of)."""
        end = pd.Timestamp(as_of, tz="UTC") if as_of is not None \
            else pd.Timestamp.now(tz="UTC")
        logger.info("job intraday_refresh: %d symbols %s -> %s",
                    len(symbols), timeframe, end)
        return self.ingestion.incremental_update(
            symbols, timeframe, end=end, start_if_empty=start_if_empty)

    # --------------------------------------------------------------- helpers

    def _last_session_end(self, as_of: pd.Timestamp):
        if self.calendar is None:
            return as_of
        day = as_of.date()
        if self.calendar.is_session(day):
            return as_of
        prev = self.calendar.previous_session(day)
        return pd.Timestamp(prev, tz="UTC") if prev is not None else as_of
