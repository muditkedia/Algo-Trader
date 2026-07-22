"""Strategy participation audit (§14): who scans, who does not, and why.

35 strategies are registered; 12 participate in intraday scanning. That is a
DESIGN OUTCOME, not an oversight, and this file pins the reason so it cannot
drift silently: the production engine is an INTRADAY engine - it squares off
every position at 15:15 - and a strategy whose execution spec needs weeks to
reach its horizon would be force-closed on entry day, every day.

``pytest -s`` prints the participation table.
"""


from algo.core.enums import HoldingScope
from algo.strategies.library import ALL_STRATEGIES
from algo.trading.engine import load_intraday_strategies


def _rows():
    scanning = {s.name for s in load_intraday_strategies()}
    out = []
    for cls in ALL_STRATEGIES:
        m, e = cls.meta, cls.execution
        intraday = m.holding_scope == HoldingScope.INTRADAY
        participates = m.name in scanning
        if participates:
            reason = f"intraday, squares off at the session cutoff"
        elif not m.enabled:
            reason = "disabled in its metadata"
        elif not intraday:
            reason = (f"swing scope: needs up to {e.max_hold_bars} bars "
                      f"({m.timeframe}) to reach its horizon - the intraday "
                      f"engine would close it at 15:15 on entry day")
        else:
            reason = "enabled and intraday but not loaded - INVESTIGATE"
        out.append({
            "name": m.name, "timeframe": m.timeframe,
            "scope": "INTRADAY" if intraday else "SWING",
            "enabled": m.enabled, "scanning": participates,
            "max_hold": e.max_hold_bars if e else None, "reason": reason})
    return sorted(out, key=lambda r: (not r["scanning"], r["timeframe"],
                                      r["name"]))


# ================================================================ the audit

def test_print_the_participation_table(capsys):
    rows = _rows()
    scanning = [r for r in rows if r["scanning"]]
    with capsys.disabled():
        print(f"\n\n  STRATEGY PARTICIPATION ({len(rows)} registered, "
              f"{len(scanning)} scanning)\n")
        head = (f"  {'strategy':24} {'tf':5} {'scope':9} {'enabled':8} "
                f"{'scanning':9} {'hold':6} reason")
        print(head)
        print("  " + "-" * 108)
        for r in rows:
            print(f"  {r['name']:24} {r['timeframe']:5} {r['scope']:9} "
                  f"{str(r['enabled']):8} {str(r['scanning']):9} "
                  f"{str(r['max_hold'] or '-'):6} {r['reason'][:46]}")
        by_tf = {}
        for r in scanning:
            by_tf[r["timeframe"]] = by_tf.get(r["timeframe"], 0) + 1
        print(f"\n  scanning by timeframe: {by_tf}")
        print(f"  excluded: {len(rows) - len(scanning)} "
              f"(all SWING scope - see reason column)")
    assert rows


# ============================================================== the reasons

def test_exactly_the_intraday_strategies_participate():
    scanning = {s.name for s in load_intraday_strategies()}
    expected = {c.meta.name for c in ALL_STRATEGIES
                if c.meta.enabled and c.meta.holding_scope == HoldingScope.INTRADAY}
    assert scanning == expected


def test_no_strategy_is_excluded_without_an_explanation():
    """Every non-participating strategy must have a stated reason - an
    unexplained exclusion is the case worth investigating."""
    unexplained = [r["name"] for r in _rows()
                   if not r["scanning"] and "INVESTIGATE" in r["reason"]]
    assert not unexplained, unexplained


def test_swing_strategies_are_excluded_because_of_the_squareoff():
    """The concrete justification: their specs allow overnight holds and cap
    the trade in BARS, both of which the intraday engine contradicts."""
    swing = [c for c in ALL_STRATEGIES
             if c.meta.enabled and c.meta.holding_scope != HoldingScope.INTRADAY]
    assert swing, "expected swing strategies in the registry"
    for cls in swing:
        spec = cls.execution
        assert spec.intraday is False, cls.meta.name
        assert spec.allow_overnight is True, cls.meta.name
        assert spec.max_hold_bars and spec.max_hold_bars > 1, cls.meta.name


def test_participation_is_not_a_hand_maintained_list():
    """It is derived from the registry each start, so adding a strategy needs
    no edit here - and cannot be forgotten."""
    import inspect
    source = inspect.getsource(load_intraday_strategies)
    assert "ALL_STRATEGIES" in source
    assert "HoldingScope.INTRADAY" in source
    assert "meta.enabled" in source


def test_every_scanning_strategy_declares_an_execution_spec():
    for strat in load_intraday_strategies():
        assert strat.execution is not None, strat.name
        assert strat.execution.intraday is True, strat.name


def test_every_scanning_strategy_has_a_display_name():
    """The dashboard shows strategy names; a missing one renders as a raw id."""
    from algo.trading.dashboard import ENTRY_RULES, STRATEGY_NAMES
    for strat in load_intraday_strategies():
        assert strat.name in STRATEGY_NAMES, strat.name
        assert strat.name in ENTRY_RULES, strat.name


def test_the_engine_only_fetches_timeframes_that_are_scanned():
    """No timeframe should be downloaded that no participating strategy uses."""
    from algo.trading.config import TradingConfig
    strategies = load_intraday_strategies()
    declared = sorted({s.meta.timeframe for s in strategies})
    assert TradingConfig().effective_timeframes(declared) == tuple(declared)
    # a config asking for an unused timeframe cannot add it
    restricted = TradingConfig.from_dict({"timeframes": ["1d"]})
    assert "1d" not in restricted.effective_timeframes(declared)
