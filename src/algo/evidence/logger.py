"""Evidence Logger - the single write path into the evidence database.

Everything the platform observes is persisted through here: runs, instruments,
strategy registrations and status transitions, and - the core of the system -
every signal (whatever its disposition), its matured outcome, and any resulting
trade. This is the upgrade of the crypto project's ``RejectionRecorder`` (which
only logged NO-GO trades to JSONL) into a full, queryable evidence store that
records *every* signal, traded or not.

The logger holds no analytics logic; it only writes rows faithfully.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable, List, Optional

from algo.core.logging import get_logger
from algo.evidence.database import EvidenceDB
from algo.evidence.models import (
    Run, Signal, SignalOutcome, StrategyRecord, StrategyStatus, TradeRecord,
)

logger = get_logger("evidence.logger")


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string (the evidence timestamp format)."""
    return datetime.now(timezone.utc).isoformat()


def _encode(value):
    """Make a Python value bindable by sqlite3."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str)
    return value


def _insert(conn, table: str, data: dict, pk: str) -> int:
    """Insert a row (dropping an unset autoincrement PK); return its rowid."""
    payload = {k: _encode(v) for k, v in data.items()
               if not (k == pk and v is None)}
    columns = ", ".join(payload)
    placeholders = ", ".join(["?"] * len(payload))
    cursor = conn.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
        list(payload.values()),
    )
    return int(cursor.lastrowid)


class EvidenceLogger:
    """Writes evidence rows into an ``EvidenceDB``."""

    def __init__(self, db: EvidenceDB) -> None:
        self.db = db
        self.conn = db.connection

    # -------------------------------------------------------------------- runs

    def start_run(self, kind: str, **fields) -> Run:
        run = Run(kind=kind, started_at=fields.pop("started_at", now_iso()),
                  **fields)
        with self.conn:
            run.run_id = _insert(self.conn, "runs", asdict(run), "run_id")
        return run

    def finish_run(self, run_id: int, notes: Optional[str] = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET finished_at = ?, notes = COALESCE(?, notes) "
                "WHERE run_id = ?", (now_iso(), notes, run_id))

    # ----------------------------------------------------------- instruments

    def upsert_instrument(self, symbol: str, **fields) -> None:
        cols = {"symbol": symbol, **fields}
        assignments = ", ".join(f"{k} = excluded.{k}" for k in fields)
        columns = ", ".join(cols)
        placeholders = ", ".join(["?"] * len(cols))
        sql = (f"INSERT INTO instruments ({columns}) VALUES ({placeholders}) "
               f"ON CONFLICT(symbol) DO UPDATE SET {assignments}"
               if fields else
               f"INSERT OR IGNORE INTO instruments ({columns}) "
               f"VALUES ({placeholders})")
        with self.conn:
            self.conn.execute(sql, [_encode(v) for v in cols.values()])

    # ------------------------------------------------------------- strategies

    def register_strategy(self, name: str, version: str, **fields) -> int:
        """Return the strategy_id for (name, version), inserting if new."""
        existing = self.conn.execute(
            "SELECT strategy_id FROM strategies WHERE name = ? AND version = ?",
            (name, version)).fetchone()
        if existing:
            return int(existing[0])
        record = StrategyRecord(name=name, version=version,
                                created_at=now_iso(), **fields)
        with self.conn:
            record.strategy_id = _insert(
                self.conn, "strategies", asdict(record), "strategy_id")
            self.conn.execute(
                "INSERT INTO strategy_events (strategy_id, ts, from_status, "
                "to_status, reason) VALUES (?, ?, ?, ?, ?)",
                (record.strategy_id, now_iso(), None, record.status,
                 "registered"))
        return record.strategy_id

    def set_strategy_status(self, strategy_id: int, to_status: str,
                            reason: Optional[str] = None) -> None:
        row = self.conn.execute(
            "SELECT status FROM strategies WHERE strategy_id = ?",
            (strategy_id,)).fetchone()
        from_status = row[0] if row else None
        with self.conn:
            self.conn.execute(
                "UPDATE strategies SET status = ?, status_reason = ?, "
                "status_at = ? WHERE strategy_id = ?",
                (_encode(to_status), reason, now_iso(), strategy_id))
            self.conn.execute(
                "INSERT INTO strategy_events (strategy_id, ts, from_status, "
                "to_status, reason) VALUES (?, ?, ?, ?, ?)",
                (strategy_id, now_iso(), from_status, _encode(to_status),
                 reason))

    # ---------------------------------------------------------------- signals

    def record_signal(self, signal: Signal) -> int:
        """Persist one signal unconditionally; return its signal_id.

        This is the non-negotiable core of the platform: every candidate any
        strategy emits is stored, whatever its disposition.
        """
        with self.conn:
            signal.signal_id = _insert(
                self.conn, "signals", asdict(signal), "signal_id")
        return signal.signal_id

    def signal_exists(self, strategy_id: int, symbol: str, ts: str,
                      mode: str) -> bool:
        """True when this exact signal is already recorded - the duplicate
        guard for scan cycles that re-observe the same bar."""
        row = self.conn.execute(
            "SELECT 1 FROM signals WHERE strategy_id = ? AND symbol = ? "
            "AND ts = ? AND mode = ? LIMIT 1",
            (strategy_id, symbol, ts, _encode(mode))).fetchone()
        return row is not None

    def find_signal(self, strategy_id: int, symbol: str, ts: str,
                    mode: str) -> Optional[int]:
        """signal_id of an exact recorded signal, or None."""
        row = self.conn.execute(
            "SELECT signal_id FROM signals WHERE strategy_id = ? AND "
            "symbol = ? AND ts = ? AND mode = ? LIMIT 1",
            (strategy_id, symbol, ts, _encode(mode))).fetchone()
        return int(row[0]) if row else None

    def update_disposition(self, signal_id: int, disposition: str,
                           reason: Optional[str] = None) -> None:
        """Upgrade a signal's disposition (e.g. recorded_only -> executed when
        the paper engine actually takes it)."""
        with self.conn:
            self.conn.execute(
                "UPDATE signals SET disposition = ?, disposition_reason = "
                "COALESCE(?, disposition_reason) WHERE signal_id = ?",
                (_encode(disposition), reason, signal_id))

    def update_rank(self, signal_id: int, rank: int) -> None:
        """Persist the latest ranking decision for an already-recorded signal
        (re-scans re-rank; the signal row keeps the most recent rank)."""
        with self.conn:
            self.conn.execute(
                "UPDATE signals SET rank_in_scan = ? WHERE signal_id = ?",
                (rank, signal_id))

    def record_signals(self, signals: Iterable[Signal]) -> List[int]:
        ids = []
        with self.conn:
            for signal in signals:
                signal.signal_id = _insert(
                    self.conn, "signals", asdict(signal), "signal_id")
                ids.append(signal.signal_id)
        return ids

    # --------------------------------------------------------------- outcomes

    def record_outcome(self, outcome: SignalOutcome) -> None:
        """Insert/replace the matured outcome for a signal (labeler write)."""
        self.record_outcomes([outcome])

    def record_outcomes(self, outcomes: Iterable[SignalOutcome]) -> int:
        """Batch-write matured outcomes in ONE transaction.

        A per-row transaction costs a disk sync each; at ~60k signals per
        strategy that dominates the labeling run.
        """
        rows = list(outcomes)
        if not rows:
            return 0
        payloads = []
        for outcome in rows:
            if outcome.labeled_at is None:
                outcome.labeled_at = now_iso()
            payloads.append({k: _encode(v)
                             for k, v in asdict(outcome).items()})
        columns = ", ".join(payloads[0])
        placeholders = ", ".join(["?"] * len(payloads[0]))
        with self.conn:
            self.conn.executemany(
                f"INSERT OR REPLACE INTO signal_outcomes ({columns}) "
                f"VALUES ({placeholders})",
                [list(p.values()) for p in payloads])
        return len(rows)

    # ----------------------------------------------------------------- trades

    def record_trade(self, trade: TradeRecord) -> int:
        with self.conn:
            trade.trade_id = _insert(
                self.conn, "trades", asdict(trade), "trade_id")
        return trade.trade_id

    # ------------------------------------------------------------ evaluations

    def record_evaluation(self, strategy_id: int, scope_type: str,
                          scope_value: str, **fields) -> int:
        data = {"strategy_id": strategy_id, "as_of": fields.pop("as_of", now_iso()),
                "scope_type": scope_type, "scope_value": scope_value, **fields}
        with self.conn:
            return _insert(self.conn, "evaluations", data, "eval_id")

    # ---------------------------------------------------------- regime labels

    def record_regime(self, day: str, symbol: str,
                      trend_label: Optional[str] = None,
                      vol_label: Optional[str] = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO regime_daily "
                "(date, symbol, trend_label, vol_label) VALUES (?, ?, ?, ?)",
                (day, symbol, trend_label, vol_label))
