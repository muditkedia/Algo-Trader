"""Research Engine and the reused validation package.

The Research Engine turns accumulated evidence into strategy verdicts: it
measures forward-return edge against costs, runs the validation battery
(metrics, Monte Carlo, walk-forward, sensitivity, stress, portfolio, regime),
and produces the standardized report. The ``validation`` sub-package is reused
verbatim from the crypto phase, adapted only to be market-agnostic.
"""
