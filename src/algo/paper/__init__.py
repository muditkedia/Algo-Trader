"""Paper trading - simulated execution against real (stored) market data.

No orders are ever placed anywhere. The engine reuses the scanner, the
strategies' confidence, the promoted risk engine (stop ratchet), the NSE cost
model, and the evidence logger; positions live in a local JSON state file and
every signal/decision/trade is recorded to evidence with mode="paper".
"""

from algo.paper.engine import PaperEngine, PaperPosition

__all__ = ["PaperEngine", "PaperPosition"]
