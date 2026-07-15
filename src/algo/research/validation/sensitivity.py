"""Parameter sensitivity tooling (VALIDATION_RULES SS13).

Three responsibilities, cleanly separated because backtests are not run yet:

1. ``build_grid()``     - enumerate every configurable threshold from
   algo_core.settings and produce one-at-a-time perturbation points at
   x{0.80, 0.90, 0.95, 1.05, 1.10, 1.20} with protocol rules applied
   (integer rounding with +/-1 substitution for periods, hard_stop capped
   at 6%, sanity-violating points skipped with reasons).
2. ``plan_backtests()`` - emit the config-override files + backtest commands
   for later execution (Phase E). Nothing is executed here.
3. ``aggregate()``      - given per-point metric records (from future runs),
   build sensitivity curves, the tornado ranking, and the plateau/cliff
   verdict per SS13.

Market-agnostic: ``build_grid`` is handed the frozen-dataclass parameter
instances to perturb (a strategy's own settings), so this module has no
dependency on any specific strategy or market. The caller decides what to
sweep.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Optional

from .logging_utils import get_logger

logger = get_logger("sensitivity")

MULTIPLIERS = (0.80, 0.90, 0.95, 1.05, 1.10, 1.20)
HARD_STOP_CAP = 0.06
#: Key metrics tracked at every grid point (recorded from future backtests).
TRACKED_METRICS = (
    "profit_factor", "sharpe", "sortino", "max_drawdown_pct",
    "expectancy", "trades", "holding_median",
)


@dataclass
class GridPoint:
    bucket: str            # "engine" | "risk" | "indicators"
    param: str
    base_value: float
    multiplier: float
    value: float
    override: dict         # config.algo_trader override for this point
    skipped: Optional[str] = None  # reason, when the point is invalid


def build_grid(settings_instances: dict, multipliers: tuple = MULTIPLIERS) -> list:
    """One-at-a-time perturbation grid over every numeric threshold.

    ``settings_instances``: mapping of bucket name (e.g. "engine", "risk",
    "indicators") to a frozen dataclass instance whose numeric fields are the
    thresholds to perturb. The caller supplies the strategy's own settings, so
    this module stays market- and strategy-agnostic.
    """
    instances = settings_instances
    grid: list = []
    for bucket, instance in instances.items():
        for spec in fields(type(instance)):
            base = getattr(instance, spec.name)
            if isinstance(base, bool):
                continue  # toggles are not thresholds
            if spec.name == "profit_lock_tiers":
                grid.extend(_tier_points(bucket, spec.name, base, multipliers))
                continue
            if not isinstance(base, (int, float)):
                continue
            for mult in multipliers:
                grid.append(_numeric_point(bucket, spec.name, base, mult))
    kept = sum(1 for p in grid if p.skipped is None)
    logger.info("sensitivity grid: %d points (%d kept, %d skipped)",
                len(grid), kept, len(grid) - kept)
    return grid


def _numeric_point(bucket: str, name: str, base, mult: float) -> GridPoint:
    skipped = None
    if isinstance(base, int):
        value = int(round(base * mult))
        if value == base:  # +/-5% rounds away on small periods: step +/-1
            value = base + (1 if mult > 1 else -1)
        if value < 1:
            skipped = "period below 1"
    else:
        value = round(base * mult, 10)

    if name == "hard_stop_pct" and value > HARD_STOP_CAP:
        value, skipped = HARD_STOP_CAP, "capped at 6% hard stop (safety)"
    if name in ("rsi_min", "rsi_max", "rsi_ideal") and not 0 < value < 100:
        skipped = "RSI bound outside (0, 100)"
    if isinstance(value, float) and value < 0:
        skipped = "negative threshold"

    return GridPoint(
        bucket=bucket, param=name, base_value=float(base), multiplier=mult,
        value=float(value), skipped=skipped,
        override={"algo_trader": {bucket: {name: value}}},
    )


def _tier_points(bucket: str, name: str, tiers: tuple, multipliers) -> list:
    """Scale the whole profit-lock ladder per multiplier (SS13)."""
    points = []
    for mult in multipliers:
        scaled = [[round(t * mult, 6), round(f * mult, 6)] for t, f in tiers]
        points.append(GridPoint(
            bucket=bucket, param=name, base_value=float(tiers[0][0]),
            multiplier=mult, value=float(scaled[0][0]),
            override={"algo_trader": {bucket: {name: scaled}}},
        ))
    return points


def write_manifest(grid: list, path: Path) -> Path:
    """Persist the grid as JSON (audit trail + input to plan_backtests)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [{
        "bucket": p.bucket, "param": p.param, "base_value": p.base_value,
        "multiplier": p.multiplier, "value": p.value, "skipped": p.skipped,
        "override": p.override,
    } for p in grid]
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    logger.info("sensitivity manifest written: %s (%d points)", path, len(payload))
    return path


def plan_backtests(
    grid: list,
    timerange: str,
    overrides_dir: Path,
    base_config: str = "user_data/config.json",
    fee: float = 0.001,
) -> list:
    """Write per-point override configs and return the command list.

    Freqtrade deep-merges multiple ``--config`` files, so each point is the
    base config plus one small override file. NOTHING IS EXECUTED HERE.
    """
    overrides_dir = Path(overrides_dir)
    overrides_dir.mkdir(parents=True, exist_ok=True)
    commands = []
    for i, point in enumerate(grid):
        if point.skipped:
            continue
        override_path = overrides_dir / (
            f"{i:04d}_{point.param}_{point.multiplier:.2f}.json"
        )
        override_path.write_text(json.dumps(point.override, indent=1),
                                 encoding="utf-8")
        export = f"user_data/backtest_results/sens_{i:04d}.json"
        commands.append(
            "docker compose run --rm freqtrade backtesting "
            f"--config {base_config} --config user_data/{override_path.name} "
            "--strategy AdaptiveTrendStrategy --timeframe-detail 1m "
            f"--fee {fee} --timerange {timerange} "
            f"--export trades --export-filename {export}"
        )
    logger.info("planned %d sensitivity backtests (not executed)", len(commands))
    return commands


# ------------------------------------------------------------- aggregation

def aggregate(records: list, floors: Optional[dict] = None) -> dict:
    """Curves + tornado + plateau/cliff verdict from per-point metrics.

    ``records``: dicts with keys {param, multiplier, <TRACKED_METRICS>},
    including one baseline record per param at multiplier 1.0.
    ``floors``: SS7 minimums, e.g. {"profit_factor": 1.25, "sharpe": 1.0}.
    """
    floors = floors or {"profit_factor": 1.25, "sharpe": 1.0}
    by_param: dict = {}
    for record in records:
        by_param.setdefault(record["param"], []).append(record)

    curves, tornado, cliffs = {}, [], []
    for param, rows in by_param.items():
        rows = sorted(rows, key=lambda r: r["multiplier"])
        curves[param] = [
            {"multiplier": r["multiplier"],
             **{k: r.get(k) for k in TRACKED_METRICS if k in r}}
            for r in rows
        ]
        base = next((r for r in rows if r["multiplier"] == 1.0), None)
        swing = 0.0
        for metric in ("sharpe", "profit_factor"):
            values = [r[metric] for r in rows if metric in r and r[metric] is not None]
            if len(values) >= 2:
                swing = max(swing, max(values) - min(values))
        tornado.append({"param": param, "swing": round(swing, 4)})
        cliffs.extend(_find_cliffs(param, rows, base, floors))

    tornado.sort(key=lambda t: -t["swing"])
    verdict = "plateau (accept)" if not cliffs else "cliff (reject)"
    return {"curves": curves, "tornado": tornado, "cliffs": cliffs,
            "verdict": verdict}


def _find_cliffs(param: str, rows: list, base: Optional[dict],
                 floors: dict) -> list:
    """SS13 cliff rule: a +/-5% or +/-10% move that breaks a floor or drops
    a key metric by more than 50% relative to baseline."""
    cliffs = []
    for row in rows:
        if abs(row["multiplier"] - 1.0) > 0.101 or row["multiplier"] == 1.0:
            continue
        for metric, floor in floors.items():
            value = row.get(metric)
            if value is None:
                continue
            broke_floor = value < floor
            halved = bool(
                base and base.get(metric) and base[metric] > 0
                and value < 0.5 * base[metric]
            )
            if broke_floor or halved:
                cliffs.append({
                    "param": param, "multiplier": row["multiplier"],
                    "metric": metric, "value": value,
                    "reason": "below floor" if broke_floor else ">50% drop",
                })
    return cliffs
