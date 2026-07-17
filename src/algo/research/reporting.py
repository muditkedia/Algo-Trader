"""Evidence rendering - one home for the research pipeline's human output.

Every candidate that goes through the pipeline gets the SAME league table and
the SAME verdict detail, whatever entry point ran it. That rendering lives here
rather than inside a script so a new entry point inherits it for free and no
second, drifting copy of "how we report a verdict" can appear.

Nothing here decides anything: verdicts are issued by the research engine
against the pre-registered bars. This module only displays them.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd

#: League-table columns, in reading order: what fired, what it earned, and the
#: two numbers the D-007 gate actually turns on (edge_ci_low_bps vs cost_bps).
LEAGUE_COLUMNS = ("strategy", "verdict", "n_signals", "trades", "expectancy",
                  "win_rate", "profit_factor", "sharpe", "edge_bps",
                  "edge_ci_low_bps", "cost_bps", "median_hold_min", "conf_corr")


def league_table_text(table: pd.DataFrame,
                      columns: Sequence[str] = LEAGUE_COLUMNS) -> str:
    """The league table as fixed-width text (console + fenced markdown)."""
    if table.empty:
        return "(no candidates measured)"
    present = [c for c in columns if c in table.columns]
    with pd.option_context("display.width", 200, "display.max_columns", 50):
        return table[present].to_string(index=False)


def verdict_detail_text(verdicts: Sequence, indent: str = "  ") -> str:
    """Every verdict with the reasons behind it. Reasons are never summarized
    away: a rejection the owner cannot audit is not evidence."""
    lines = []
    for verdict in verdicts:
        lines.append(f"{indent}{verdict.strategy}: {verdict.verdict}")
        lines += [f"{indent}  - {reason}" for reason in verdict.reasons]
    return "\n".join(lines)


def league_table_markdown(table: pd.DataFrame, verdicts: Sequence, *,
                          data_note: str, title: str = "League table") -> str:
    """The persisted evidence report for a sweep."""
    counts = {}
    for verdict in verdicts:
        counts[verdict.verdict] = counts.get(verdict.verdict, 0) + 1
    tally = ", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "none"

    lines = [f"# {title}", "", f"_{data_note}_", "",
             f"_{len(verdicts)} candidate(s) measured: {tally}._", "",
             "```", league_table_text(table), "```", "",
             "## Verdict detail", ""]
    for verdict in verdicts:
        lines.append(f"### {verdict.strategy}: {verdict.verdict}")
        if verdict.n_recorded is not None:
            lines.append(f"- evidence: {verdict.n_recorded} signals recorded, "
                         f"{verdict.n_labeled} outcomes labeled")
        lines += [f"- {reason}" for reason in verdict.reasons] + [""]
    return "\n".join(lines)
