"""General structured logging for the platform.

A single, minimal setup used by every sub-package (evidence, scanner, research
engine, ...). Console logging is always on; an optional file sink can be added.

This is intentionally NOT the same thing as the validation package's
``logging_utils`` - that module instruments validation *steps* (timed JSONL
step records for the report pipeline) and is specific to that concern. This one
just configures ordinary Python loggers for the rest of the platform. They are
different responsibilities, not duplicates.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

_ROOT = "algo"
_CONFIGURED = False


def configure(
    level: int = logging.INFO,
    logfile: Optional[Union[str, Path]] = None,
    fmt: str = "%(asctime)s %(name)s %(levelname)s %(message)s",
) -> None:
    """Configure the ``algo`` logger tree once. Idempotent.

    Adds a console handler always, and a file handler when ``logfile`` is given.
    Repeated calls do not stack duplicate handlers.
    """
    global _CONFIGURED
    root = logging.getLogger(_ROOT)
    root.setLevel(level)
    root.propagate = False

    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
               for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(fmt))
        root.addHandler(console)

    if logfile is not None:
        path = Path(logfile)
        already = any(
            isinstance(h, logging.FileHandler)
            and Path(getattr(h, "baseFilename", "")) == path.resolve()
            for h in root.handlers
        )
        if not already:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(logging.Formatter(fmt))
            root.addHandler(handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ``algo`` tree (e.g. ``algo.evidence``)."""
    if not name.startswith(_ROOT):
        name = f"{_ROOT}.{name}"
    return logging.getLogger(name)
