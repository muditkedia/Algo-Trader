"""Strategy plugin interface and registry.

A strategy is a plug-in: it declares only its entry signal and metadata
(required indicators, direction, holding scope, regime hypothesis, known
failure modes). Everything else - measurement, confidence, risk, execution -
is common platform machinery. Strategies never share mutable state, so a new
one can be added without affecting any existing one.
"""
