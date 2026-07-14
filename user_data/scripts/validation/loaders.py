"""Load Freqtrade backtest exports into the canonical trades frame.

Canonical trade schema (pandas DataFrame, one row per closed trade):

    pair            str
    open_date       tz-aware UTC timestamp
    close_date      tz-aware UTC timestamp
    profit_ratio    float   net per-trade return (fees included by Freqtrade)
    profit_abs      float   net absolute profit in stake currency
    stake_amount    float
    trade_duration  float   minutes (derived when absent)
    exit_reason     str     (optional, "" when absent)
    enter_tag       str     (optional)
    stop_distance_pct float (optional; NaN when unknown)

Loading prefers Freqtrade's own ``load_backtest_data`` (correct across export
format versions); a plain-JSON fallback parses the documented structure so the
module stays usable outside the container. NOTE: the fallback is verified
against a synthetic fixture; verification against a real export happens at the
smoke-test phase (no backtests have been run yet by design).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from validation.logging_utils import get_logger

logger = get_logger("loaders")

REQUIRED_COLUMNS = (
    "pair", "open_date", "close_date", "profit_ratio", "profit_abs",
    "stake_amount",
)
OPTIONAL_DEFAULTS = {
    "exit_reason": "",
    "enter_tag": "",
    "stop_distance_pct": float("nan"),
}


def ensure_trades(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize a trades frame to the canonical schema (returns a copy).

    Raises ValueError when a required column is missing.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"trades frame missing required columns: {missing}")
    trades = frame.copy()
    for column in ("open_date", "close_date"):
        trades[column] = pd.to_datetime(trades[column], utc=True)
    for column, default in OPTIONAL_DEFAULTS.items():
        if column not in trades.columns:
            trades[column] = default
    if "trade_duration" not in trades.columns:
        trades["trade_duration"] = (
            (trades["close_date"] - trades["open_date"]).dt.total_seconds() / 60.0
        )
    trades = trades.sort_values("close_date").reset_index(drop=True)
    return trades


def load_backtest_export(path: Union[str, Path]) -> pd.DataFrame:
    """Load a Freqtrade ``--export trades`` file into the canonical schema."""
    path = Path(path)
    trades = _load_via_freqtrade(path)
    if trades is None:
        trades = _load_via_json(path)
    logger.info("loaded %d trades from %s", len(trades), path)
    return ensure_trades(trades)


def _load_via_freqtrade(path: Path) -> Optional[pd.DataFrame]:
    """Use Freqtrade's loader when the library is importable (container)."""
    try:
        from freqtrade.data.btanalysis import load_backtest_data
    except ImportError:
        return None
    try:
        return load_backtest_data(path)
    except Exception as exc:
        logger.warning("freqtrade loader failed (%s); using JSON fallback", exc)
        return None


def _load_via_json(path: Path) -> pd.DataFrame:
    """Plain-JSON fallback for the documented export structure."""
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict) and "strategy" in payload:
        strategies = payload["strategy"]
        name = next(iter(strategies))
        records = strategies[name]["trades"]
    elif isinstance(payload, list):
        records = payload
    else:
        raise ValueError(f"unrecognized backtest export structure: {path}")
    return pd.DataFrame(records)


def load_rejections(path: Union[str, Path]) -> pd.DataFrame:
    """Load the GO/NO-GO rejection audit JSONL into a frame (empty if absent)."""
    path = Path(path)
    if not path.exists():
        return pd.DataFrame()
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("skipping malformed rejection line")
    return pd.DataFrame(records)


def load_freqtrade_config(path: Union[str, Path]) -> dict:
    """Load config.json (which may contain // comments) tolerantly."""
    path = Path(path)
    try:
        from freqtrade.configuration.load_config import load_config_file
        return load_config_file(str(path))
    except ImportError:
        pass
    text = path.read_text(encoding="utf-8")
    stripped = []
    for line in text.splitlines():
        candidate = line.strip()
        if candidate.startswith("//"):
            continue
        stripped.append(line)
    return json.loads("\n".join(stripped))
