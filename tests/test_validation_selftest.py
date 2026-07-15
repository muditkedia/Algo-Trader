"""Reuse verification: the relocated validation package still fully self-tests.

This runs the entire validation battery's own self-test (metrics, Monte Carlo,
walk-forward, sensitivity, regime, stress, portfolio, report, coordinator) on
synthetic data inside pytest, guaranteeing the reused infrastructure stays green
after the market-agnostic relocation.
"""

from algo.research.validation import selftest


def test_validation_package_selftest_passes():
    assert selftest.main() == 0
