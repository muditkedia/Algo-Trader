"""EventLog - append-only structured journal for the production system.

One JSONL file per day (`events-YYYYMMDD.jsonl` under the state dir): every
order intent, fill, risk decision, recovery action, error and health beat is
one line with a UTC timestamp, an event type, and a payload. This is the
audit trail recovery and the daily summary read from; it is append-only and
flushed per event (crash-safe).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd

from algo.core.logging import get_logger

logger = get_logger("trading.events")

#: canonical event types (free strings allowed; these are the conventions)
SIGNAL = "signal"
RISK_BLOCK = "risk_block"
ORDER = "order"
FILL = "fill"
POSITION = "position"
RECOVERY = "recovery"
ERROR = "error"
HEALTH = "health"
HALT = "halt"


class EventLog:
    def __init__(self, state_dir) -> None:
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, day: Optional[str] = None) -> Path:
        day = day or pd.Timestamp.now(tz="UTC").strftime("%Y%m%d")
        return self.dir / f"events-{day}.jsonl"

    def emit(self, event_type: str, **payload) -> dict:
        record = {"ts": pd.Timestamp.now(tz="UTC").isoformat(),
                  "type": event_type, **payload}
        line = json.dumps(record, default=str)
        with self._path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        logger.info("%s %s", event_type,
                    {k: v for k, v in payload.items() if k != "detail"})
        return record

    def read_day(self, day: Optional[str] = None) -> list:
        path = self._path(day)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        records = []
        for index, raw in enumerate(lines):
            line = raw.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                # A process can be interrupted after writing part of the
                # final record. That one record is unrecoverable, but all
                # earlier records remain authoritative. Interior corruption
                # must still surface instead of being silently hidden.
                is_final_partial = (index == len(lines) - 1
                                    and not raw.endswith(("\n", "\r")))
                if is_final_partial:
                    logger.warning("event log ends with an incomplete record: %s",
                                   path)
                    break
                raise
        return records
