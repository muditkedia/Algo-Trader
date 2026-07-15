"""Structured logging for validation runs.

Every validation step emits console lines and, when a run directory is
configured, structured JSONL records (one JSON object per line) suitable for
later auditing:

    {"ts": "...", "level": "INFO", "step": "portfolio", "event": "end",
     "status": "ok", "duration_s": 0.42, "detail": {...}}

Usage:
    log = get_logger("validation.portfolio")
    with step(log, "portfolio", detail={"trades": len(df)}):
        ...
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_JSONL_PATH: Optional[Path] = None


def configure(jsonl_path: Optional[Path] = None, level: int = logging.INFO) -> None:
    """Configure validation logging once per run.

    ``jsonl_path`` enables the structured JSONL sink; console logging is
    always on. Safe to call repeatedly (idempotent handlers).
    """
    global _JSONL_PATH
    root = logging.getLogger("validation")
    root.setLevel(level)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(
            logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
        )
        root.addHandler(console)
    if jsonl_path:
        _JSONL_PATH = Path(jsonl_path)
        _JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_logger(name: str) -> logging.Logger:
    if not name.startswith("validation"):
        name = f"validation.{name}"
    return logging.getLogger(name)


def emit(step_name: str, event: str, status: str = "ok", **detail) -> None:
    """Append one structured record to the JSONL sink (if configured)."""
    if _JSONL_PATH is None:
        return
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "step": step_name,
        "event": event,
        "status": status,
        "detail": detail or {},
    }
    try:
        with _JSONL_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
    except OSError:  # logging must never break validation
        pass


@contextmanager
def step(logger: logging.Logger, name: str, **detail):
    """Log the start/end/duration/status of one validation step."""
    logger.info("step %s: start %s", name, detail or "")
    emit(name, "start", **detail)
    started = time.monotonic()
    try:
        yield
    except Exception as exc:
        duration = round(time.monotonic() - started, 3)
        logger.error("step %s: FAILED after %.3fs - %s", name, duration, exc)
        emit(name, "end", status="error", duration_s=duration, error=str(exc))
        raise
    duration = round(time.monotonic() - started, 3)
    logger.info("step %s: done in %.3fs", name, duration)
    emit(name, "end", status="ok", duration_s=duration)
