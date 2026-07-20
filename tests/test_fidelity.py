"""Phase 17 - fidelity evaluation engine: execution semantics, pinned.

Research-only engine (algo/research/fidelity.py). These tests pin every
published-execution convention: trigger fills (incl. gap-open), no-touch no-fill,
next-open mode, honest gap-through stops, fixed-R and column targets, the
stop-before-target same-bar convention, partial + breakeven, EOD square-off, and
the floor-pivot math. gross_ratio assertions are pre-cost, so they are exact.
"""

import numpy as np
import pandas as pd
import pytest

from algo.core.costs import NseEquityCostModel
from algo.research.fidelity import (
    ExecutionSpec, floor_pivots, simulate_fidelity,
)
from algo.risk.engine import RiskParams

COST = NseEquityCostModel()
P = RiskParams()


def _bars(rows, day="2024-03-04"):
    """rows: list of (open, high, low, close) plus optional extra columns."""
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    frame["volume"] = 1000.0
    frame["date"] = pd.date_range(f"{day} 09:15", periods=len(frame),
                                  freq="15min", tz="UTC")
    return frame


def _sim(bars, spec, i=1, atr=1.0):
    return simulate_fidelity(bars, i, spec=spec, atr=atr, cost_model=COST,
                             params=P)


def _with(bars, **cols):
    out = bars.copy()
    for k, v in cols.items():
        out[k] = v
    return out


# ------------------------------------------------------------ entry modes

def test_trigger_entry_fills_at_the_level_not_the_close():
    bars = _with(_bars([(100, 100.5, 99.5, 100),
                        (100.2, 104, 100, 103.5),      # signal bar, runs to 103.5
                        (103, 104, 102, 103)]),
                 trig=101.0, stp=99.0)
    spec = ExecutionSpec(name="t", entry_mode="trigger", trigger_col="trig",
                         stop_mode="column", stop_col="stp")
    trade = _sim(bars, spec)
    assert trade["entry_price"] == 101.0           # the level, not 103.5


def test_trigger_gap_open_above_level_fills_at_open():
    bars = _with(_bars([(100, 100.5, 99.5, 100),
                        (102.5, 104, 102, 103.5),      # opens ABOVE the trigger
                        (103, 104, 102, 103)]),
                 trig=101.0, stp=99.0)
    spec = ExecutionSpec(name="t", entry_mode="trigger", trigger_col="trig",
                         stop_mode="column", stop_col="stp")
    assert _sim(bars, spec)["entry_price"] == 102.5


def test_trigger_no_touch_no_fill():
    bars = _with(_bars([(100, 100.5, 99.5, 100),
                        (100, 100.8, 99.8, 100.5),     # high < trigger
                        (100, 101, 99, 100)]),
                 trig=101.0, stp=99.0)
    spec = ExecutionSpec(name="t", entry_mode="trigger", trigger_col="trig",
                         stop_mode="column", stop_col="stp")
    assert _sim(bars, spec) is None


def test_next_open_mode_and_close_mode():
    bars = _with(_bars([(100, 101, 99, 100),
                        (100, 102, 100, 101.5),        # signal bar
                        (101.8, 103, 101, 102)]),
                 stp=99.0)
    close_spec = ExecutionSpec(name="c", entry_mode="close",
                               stop_mode="column", stop_col="stp")
    no_spec = ExecutionSpec(name="n", entry_mode="next_open",
                            stop_mode="column", stop_col="stp")
    assert _sim(bars, close_spec)["entry_price"] == 101.5   # production parity
    assert _sim(bars, no_spec)["entry_price"] == 101.8


# ------------------------------------------------------------- exits

def test_structural_stop_gaps_fill_at_the_open():
    """HONEST gap-through: unlike the production simulator, a bar that OPENS
    below the stop fills at the open, not at the stop."""
    bars = _with(_bars([(100, 101, 99, 100),
                        (100, 102, 100, 101),          # entry at close 101
                        (97.0, 98.5, 96.5, 97.5)]),    # gaps open below stop 99
                 stp=99.0)
    spec = ExecutionSpec(name="s", entry_mode="close",
                         stop_mode="column", stop_col="stp")
    trade = _sim(bars, spec)
    assert trade["exit_reason"] == "stop_loss"
    assert trade["exit_price"] == 97.0             # the open, not 99


def test_fixed_r_target_fills_at_the_level():
    # entry 100 (close), stop 98 -> risk 2 -> 2R target = 104
    bars = _with(_bars([(100, 101, 99, 100),
                        (100, 100.5, 99.5, 100),       # entry
                        (100, 105, 100, 104.5)]),      # runs through 104
                 stp=98.0)
    spec = ExecutionSpec(name="r", entry_mode="close", stop_mode="column",
                         stop_col="stp", target_mode="r", target_r=2.0)
    trade = _sim(bars, spec)
    assert trade["exit_reason"] == "target"
    assert trade["exit_price"] == pytest.approx(104.0)
    assert trade["gross_ratio"] == pytest.approx(0.04)


def test_same_bar_stop_and_target_stop_wins():
    bars = _with(_bars([(100, 101, 99, 100),
                        (100, 100.5, 99.5, 100),       # entry 100, stop 98, T 104
                        (100, 105, 97, 103)]),         # bar spans BOTH
                 stp=98.0)
    spec = ExecutionSpec(name="a", entry_mode="close", stop_mode="column",
                         stop_col="stp", target_mode="r", target_r=2.0)
    trade = _sim(bars, spec)
    assert trade["exit_reason"] == "stop_loss"      # conservative convention


def test_partial_then_second_target_with_breakeven():
    # entry 100, stop 98; T1 col 104 (book 50%, BE stop); T2 col 108
    bars = _with(_bars([(100, 101, 99, 100),
                        (100, 100.5, 99.5, 100),        # entry
                        (100, 104.5, 100, 104),         # hits T1
                        (104, 108.5, 103.5, 108)]),     # hits T2
                 stp=98.0, t1=104.0, t2=108.0)
    spec = ExecutionSpec(name="p", entry_mode="close", stop_mode="column",
                         stop_col="stp", target_mode="column", target_col="t1",
                         partial_fraction=0.5, target2_col="t2")
    trade = _sim(bars, spec)
    assert trade["partial"] is True
    assert trade["exit_reason"] == "target"
    # gross = 0.5*(104-100)/100 + 0.5*(108-100)/100 = 6%
    assert trade["gross_ratio"] == pytest.approx(0.06)


def test_partial_then_breakeven_stop_protects():
    bars = _with(_bars([(100, 101, 99, 100),
                        (100, 100.5, 99.5, 100),        # entry
                        (100, 104.5, 100, 104),         # T1 -> BE
                        (103, 103.5, 99.5, 100.5)]),    # falls through BE 100
                 stp=98.0, t1=104.0, t2=108.0)
    spec = ExecutionSpec(name="p", entry_mode="close", stop_mode="column",
                         stop_col="stp", target_mode="column", target_col="t1",
                         partial_fraction=0.5, target2_col="t2")
    trade = _sim(bars, spec)
    assert trade["exit_reason"] == "be_stop"
    # gross = 0.5*4% + 0.5*0% = 2%
    assert trade["gross_ratio"] == pytest.approx(0.02)


def test_eod_squareoff_closes_remainder():
    bars = _with(_bars([(100, 101, 99, 100),
                        (100, 100.5, 99.5, 100),        # entry, never stops/targets
                        (100, 100.6, 99.6, 100.2)]),
                 stp=95.0)
    spec = ExecutionSpec(name="e", entry_mode="close", stop_mode="column",
                         stop_col="stp")
    trade = _sim(bars, spec)
    assert trade["exit_reason"] == "session_squareoff"
    assert trade["exit_price"] == 100.2


# --------------------------------------------------------- floor pivots

def test_floor_pivots_prior_session_causal():
    d1 = _bars([(100, 112, 88, 105)], day="2024-03-04")
    d2 = _bars([(104, 106, 103, 105), (105, 107, 104, 106)], day="2024-03-05")
    frame = pd.concat([d1, d2], ignore_index=True)
    out = floor_pivots(frame)
    assert np.isnan(out["fp_pivot"].iloc[0])            # day 1: no prior
    p = (112 + 88 + 105) / 3
    assert out["fp_pivot"].iloc[1] == pytest.approx(p)
    assert out["fp_r1"].iloc[1] == pytest.approx(2 * p - 88)
    assert out["fp_r2"].iloc[1] == pytest.approx(p + (112 - 88))
