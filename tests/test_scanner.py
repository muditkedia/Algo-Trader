"""Scanner interface + pure opportunity ranking."""

import pytest

from algo.scanner.base import Opportunity, Scanner, rank_opportunities


def test_rank_opportunities_orders_and_stamps():
    opps = [
        Opportunity("A", "s", "long", 0.6, expected_reward=0.02),
        Opportunity("B", "s", "long", 0.9),
        Opportunity("C", "s", "long", 0.6, expected_reward=0.05),
    ]
    ranked = rank_opportunities(opps)
    assert [o.symbol for o in ranked] == ["B", "C", "A"]  # conf desc, reward tiebreak
    assert [o.rank for o in ranked] == [1, 2, 3]


def test_scanner_is_abstract():
    with pytest.raises(TypeError):
        Scanner()  # abstract - scan() not implemented
