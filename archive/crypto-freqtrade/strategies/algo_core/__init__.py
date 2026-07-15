"""algo_core - shared engine modules for the Algo Trader strategy framework.

Layout:
    indicators.py       Per-timeframe indicator computation (EMA/ADX/RSI/ATR/volume/structure).
    decision_engine.py  GO / NO-GO entry evaluation with scoring and rejection recording.
    risk_engine.py      Stop-loss sizing, hard-stop cap, profit-lock tiers, risk-based stakes.
    trade_manager.py    In-trade stop management and objective exit conditions.
    profiles/           Strategy profile interface, registry, and implementations.
"""
