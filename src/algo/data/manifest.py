"""Store manifest - provenance/version log for bulk data operations.

DATA_INFRASTRUCTURE_PLAN.md section 7: parquet files stay unversioned (the
store's ``normalize()`` contract is the schema); provenance is a
``manifest.json`` at the store root. Every bulk ingestion appends per-symbol
events (operation, rows fetched/added, dropped-invalid detail, provider) and
refreshes the coverage snapshot, so the store's history is inspectable
without git-tracking gigabytes. ``up_to_date``/``empty`` results refresh the
snapshot but append no event - re-runs stay noise-free.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from algo.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from algo.data.ingest import IngestionReport
    from algo.data.store import MarketDataStore

logger = get_logger("data.manifest")

MANIFEST_FILE = "manifest.json"
#: statuses that represent an actual data operation worth an event entry
EVENT_STATUSES = ("ok", "backfilled", "quarantined")


def manifest_path(store: "MarketDataStore") -> Path:
    return Path(store.root) / MANIFEST_FILE


def load(store: "MarketDataStore") -> dict:
    path = manifest_path(store)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def record(store: "MarketDataStore", report: "IngestionReport",
           provider: str) -> Path:
    """Fold one IngestionReport into the manifest. Returns the file path."""
    data = load(store)
    now = pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")
    for result in report.results:
        entry = (data.setdefault(result.symbol, {})
                 .setdefault(result.timeframe, {"events": []}))
        coverage = store.coverage(result.symbol, result.timeframe)
        if coverage:
            entry["first"] = str(coverage["start"])
            entry["last"] = str(coverage["end"])
            entry["rows"] = int(coverage["rows"])
        entry["last_update"] = now
        if result.status in EVENT_STATUSES:
            entry["events"].append({
                "ts": now, "op": result.status, "fetched": int(result.fetched),
                "added": int(result.added),
                "detail": (result.detail or "")[:200], "provider": provider,
            })
    path = manifest_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True),
                    encoding="utf-8")
    logger.info("manifest updated: %s (%d symbols)", path, len(data))
    return path
