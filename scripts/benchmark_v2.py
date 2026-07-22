"""Per-stage benchmark of Market Data Architecture v2 (§14).

Measures each pipeline stage SEPARATELY at 100/250/500/1000 symbols, never an
aggregate - an aggregate hides which stage is the wall, and the whole point of
the v2 redesign was to find that the wall is provider request-throughput, not
CPU. Stages timed:

    scheduler     plan() turning MarketState into DataRequests
    provider      transport.execute() - fetch + quality gate (CPU, synthetic
                  source so no network; the network-bound wall time is computed
                  separately from the rate limit, which is what actually binds)
    store         MarketState.apply_candles() - the write-through to parquet
    scanner+strat orchestrator.evaluate() - history read + prepare + entry
    dashboard     DashboardExporter.export() - the full snapshot set
    risk          check_day + size_for across the watchlist
    execution     paper order placement for a batch of entries

Run:  .venv/Scripts/python scripts/benchmark_v2.py            # all sizes
      .venv/Scripts/python scripts/benchmark_v2.py 100 250    # a subset

The store is synthetic and written under the scratchpad, never the real one.
"""

from __future__ import annotations

import sys
import time
import tracemalloc
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from algo.data.store import MarketDataStore
from algo.marketdata import MarketDataService, SMARTAPI_CAPABILITIES
from algo.marketdata.source import MarketDataSource
from algo.trading.clock import IST, MarketClock
from algo.trading.config import TradingConfig
from algo.trading.engine import ProductionEngine, load_intraday_strategies
from algo.trading.orchestrator import Orchestrator

TF = "15m"
BARS = 260                     # enough history for every 15m strategy
NOW = datetime(2026, 7, 20, 11, 7, tzinfo=IST)     # a Monday, mid-session
OUT = Path(__file__).resolve().parents[1] / "user_data" / "_bench"


class SyntheticSource(MarketDataSource):
    """Instant, deterministic bars - isolates CPU cost from network latency."""

    name = "synthetic-bench"

    @property
    def capabilities(self):
        return SMARTAPI_CAPABILITIES

    def fetch_candles(self, symbol, timeframe, start, end):
        opens = pd.date_range(pd.Timestamp(start).ceil("15min"),
                              pd.Timestamp(end), freq="15min", tz="UTC")
        if len(opens) == 0:
            from algo.data.ohlcv import OHLCV_COLUMNS
            return pd.DataFrame(columns=list(OHLCV_COLUMNS))
        n = len(opens)
        base = 100 + (hash(symbol) % 50)
        walk = base + np.cumsum(np.random.default_rng(len(symbol)).normal(0, .3, n))
        return pd.DataFrame({"date": opens, "open": walk, "high": walk + 0.5,
                             "low": walk - 0.5, "close": walk, "volume": 1000})


def _seed(store: MarketDataStore, symbols, up_to: pd.Timestamp) -> None:
    """Fill the store so all but the newest bar is present (the live steady
    state: one incremental bar owed per symbol per cadence)."""
    opens = pd.date_range(end=up_to, periods=BARS, freq="15min", tz="UTC")
    rng = np.random.default_rng(7)
    for s in symbols:
        walk = 100 + np.cumsum(rng.normal(0, 0.4, BARS))
        store.write(s, TF, pd.DataFrame({
            "date": opens, "open": walk, "high": walk + 1, "low": walk - 1,
            "close": walk, "volume": 1000}))


def _fmt(seconds: float) -> str:
    return f"{seconds * 1000:8.1f}" if seconds < 1 else f"{seconds:7.2f}s"


def benchmark(n: int) -> dict:
    import shutil
    root = OUT / f"n{n}"
    # start clean: a store left current from a previous run would make the
    # scheduler correctly find nothing due, and the benchmark would measure
    # an empty pass
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    store = MarketDataStore(root / "store")
    symbols = [f"SYN{i:04d}" for i in range(n)]
    # steady state: store holds up to one bar behind the expected bar
    expected_utc = pd.Timestamp("2026-07-20 05:15:00", tz="UTC")
    _seed(store, symbols, expected_utc - pd.Timedelta(minutes=15))

    clock = MarketClock.build()
    clock.now = lambda: NOW
    source = SyntheticSource()
    service = MarketDataService.build(store, source, clock, symbols, [TF],
                                      history_bars=BARS,
                                      max_requests_per_poll=n + 10,
                                      poll_budget_seconds=600)

    # ---- 1) scheduler.plan: MarketState -> DataRequests --------------------
    t = time.perf_counter()
    requests = service.scheduler.plan(service.state, at=NOW)
    t_sched = time.perf_counter() - t
    assert len(requests) == n, f"{len(requests)} requests for {n} symbols"

    # ---- 2) provider fetch + gate (CPU only) -------------------------------
    results = []
    t = time.perf_counter()
    for req in requests:
        results.append(service.transport.execute(req, now=0.0))
    t_fetch = time.perf_counter() - t

    # ---- 3) store write-through --------------------------------------------
    t = time.perf_counter()
    for req, res in zip(requests, results):
        if res.ok and res.frame is not None and not res.empty:
            service.state.apply_candles(req.symbol, TF, res.frame)
    t_store = time.perf_counter() - t

    # ---- 4) scanner + strategies -------------------------------------------
    strategies = [s for s in load_intraday_strategies()
                  if s.meta.timeframe == TF]
    orch = Orchestrator(strategies, service.state)
    t = time.perf_counter()
    signals = orch.evaluate(TF)
    t_scan = time.perf_counter() - t

    # scanner-only: the history reads without strategy evaluation
    t = time.perf_counter()
    for s in service.state.usable_symbols(TF):
        service.state.history(s, TF)
    t_scan_only = time.perf_counter() - t

    # ---- 5) dashboard export ------------------------------------------------
    (root / "syms.txt").write_text("\n".join(symbols))
    cfg = TradingConfig.from_dict({
        "mode": "paper", "symbols_file": str(root / "syms.txt"),
        "store_dir": str(root / "store"), "state_dir": str(root / "state"),
        "dashboard_dir": str(root / "dash")})
    engine = ProductionEngine(cfg, state=service.state, source=source,
                              strategies=strategies)
    engine.clock.now = lambda: NOW
    t = time.perf_counter()
    engine.exporter.export()
    t_dash = time.perf_counter() - t

    # ---- 6) risk ------------------------------------------------------------
    t = time.perf_counter()
    engine.risk.check_day(engine.portfolio)
    for sig in signals[:200]:
        engine.risk.size_for(sig, engine.portfolio)
    t_risk = time.perf_counter() - t

    # ---- 7) execution (paper) ----------------------------------------------
    t = time.perf_counter()
    for i in range(min(5, len(signals))):
        engine.orders.adapter.update_quotes({signals[i].symbol: 100.0})
    t_exec = time.perf_counter() - t

    return {
        "n": n, "scheduler": t_sched, "provider_cpu": t_fetch,
        "store": t_store, "scanner": t_scan_only,
        "scan_strat": t_scan, "dashboard": t_dash, "risk": t_risk,
        "execution": t_exec, "signals": len(signals),
        # the network-bound wall time: one request per symbol at the provider's
        # own candle rate (3/s documented) - this is what actually binds
        "fetch_wall_3ps": n / 3.0,
    }


def main() -> int:
    sizes = [int(a) for a in sys.argv[1:]] or [100, 250, 500, 1000]
    OUT.mkdir(parents=True, exist_ok=True)
    tracemalloc.start()
    rows = []
    for n in sizes:
        print(f"  building + timing {n} symbols ...", flush=True)
        rows.append(benchmark(n))
    peak_mb = tracemalloc.get_traced_memory()[1] / 1e6
    tracemalloc.stop()

    hdr = ("symbols", "scheduler", "provider", "store", "scanner",
           "scan+strat", "dashboard", "risk", "exec", "fetch@3/s")
    print("\n  PER-STAGE TIMINGS (CPU unless noted; ms except fetch@3/s)")
    print("  " + "".join(f"{h:>12}" for h in hdr))
    for r in rows:
        print("  " + "".join([
            f"{r['n']:>12}",
            f"{r['scheduler']*1000:>11.1f}",
            f"{r['provider_cpu']*1000:>11.1f}",
            f"{r['store']*1000:>11.1f}",
            f"{r['scanner']*1000:>11.1f}",
            f"{r['scan_strat']*1000:>11.1f}",
            f"{r['dashboard']*1000:>11.1f}",
            f"{r['risk']*1000:>11.1f}",
            f"{r['execution']*1000:>11.1f}",
            f"{r['fetch_wall_3ps']:>10.0f}s",
        ]))
    print(f"\n  peak Python allocation across all sizes: {peak_mb:.1f} MB")
    print(f"  signals fired: {[r['signals'] for r in rows]}")

    import json
    (OUT / "results.json").write_text(json.dumps(rows, indent=2))
    print(f"  raw results -> {OUT / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
