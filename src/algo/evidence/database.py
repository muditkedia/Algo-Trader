"""Evidence database connection + lifecycle.

``EvidenceDB`` opens (creating if needed) the SQLite file, ensures the schema
is installed, and exposes a configured connection. It is deliberately thin: the
Evidence Logger (logger.py) does the writing; queries are plain SQL / pandas.

Connections use ``sqlite3.Row`` (dict-like rows) and enable foreign keys. A
schema version is recorded in ``PRAGMA user_version``; ``migrate`` is the single
place future schema upgrades are applied.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union

from algo.core.logging import get_logger
from algo.evidence import schema as schema_mod

logger = get_logger("evidence.database")

#: In-memory sentinel for tests.
MEMORY = ":memory:"


class EvidenceDB:
    """Handle to the evidence SQLite database.

    Usage:
        with EvidenceDB("user_data/evidence/evidence.db") as db:
            db.connection.execute(...)
    """

    def __init__(self, path: Union[str, Path] = MEMORY,
                 initialize: bool = True) -> None:
        self.path = path if path == MEMORY else str(Path(path))
        if self.path != MEMORY:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if initialize:
            self.initialize()

    # ---------------------------------------------------------------- schema

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    @property
    def schema_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def initialize(self) -> None:
        """Create the schema if absent and stamp the version. Idempotent."""
        current = self.schema_version
        if current == 0:
            with self._conn:
                for statement in schema_mod.SCHEMA_STATEMENTS:
                    self._conn.execute(statement)
                self._conn.execute(
                    f"PRAGMA user_version = {schema_mod.SCHEMA_VERSION}")
            logger.info("evidence schema installed (v%d) at %s",
                        schema_mod.SCHEMA_VERSION, self.path)
        elif current < schema_mod.SCHEMA_VERSION:
            self.migrate(current, schema_mod.SCHEMA_VERSION)
        # else: up to date

    def migrate(self, from_version: int, to_version: int) -> None:
        """Apply schema migrations. No migrations exist yet (v1 is initial)."""
        raise NotImplementedError(
            f"no migration path from schema v{from_version} to v{to_version}; "
            "add one here when the schema changes."
        )

    def tables(self) -> list:
        """Names of user tables actually present (for validation/introspection)."""
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]

    # --------------------------------------------------------------- lifecycle

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "EvidenceDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
