# Performance Benchmark

_Two measurements. **v1** (Phase 6) timed the old polling feed against universe
size. **v2** (Phase 7) times each pipeline stage SEPARATELY against the
scheduler-driven subsystem — because an aggregate hides which stage is the
wall, and the whole point of v2 was to prove the wall is provider
request-throughput, not CPU._

Reproduce v2 with `scripts/benchmark_v2.py` (writes a synthetic store under
`user_data/_bench`, never the real one).

---

## v2 — per-stage timings (Phase 7)

_11 strategies on 15m, 260 bars/symbol, synthetic source (no network), one bar
owed per symbol (the live steady state). Measured 2026-07-20._

| symbols | scheduler¹ | provider² | store³ | scanner⁴ | scan+strat | dashboard | risk | fetch@3/s⁵ |
|--------:|-----------:|----------:|-------:|---------:|-----------:|----------:|-----:|-----------:|
|     100 |    0.56 s |    1.06 s |  2.08 s |   6.6 ms |    35.2 s |   83 ms |  5 ms |     33 s |
|     250 |    1.33 s |    2.68 s |  5.34 s |  15.7 ms |    88.9 s |  134 ms |  6 ms |     83 s |
|     500 |    2.61 s |    5.40 s | 10.61 s |  33.0 ms |   176.4 s |  202 ms |  7 ms |    167 s |
|    1000 |    4.97 s |   10.04 s | 20.62 s |  62.4 ms |   339.9 s |  373 ms |  6 ms |    333 s |

Peak Python allocation across all four sizes: **60 MB**. Execution (paper order
placement) is sub-millisecond and omitted.

¹ **scheduler** `plan()` on a COLD cache — it reads every symbol's stored tail
once to decide what is due. This is a one-time startup cost: after the first
poll the `(mtime, size)` cache is warm and `plan()` drops to the `scanner`
column's cost. It is not paid per cycle.
² **provider** is `transport.execute()` — fetch + quality gate, CPU only (the
synthetic source returns instantly). ~10 ms/symbol, and it overlaps with
network latency in reality.
³ **store** is the parquet write-through, paid ONLY for symbols that got a new
bar (incremental) — ~20 ms/symbol.
⁴ **scanner** is history reads on a warm cache — cache hits, negligible.
⁵ **fetch@3/s** is the network-bound wall time: one candle request per symbol
at the provider's documented 3 req/s. This is what actually binds.

### What binds, and what does not

Two stages dominate, and they are the two "walls":

- **CPU wall — `scan+strat`:** ~340 ms/symbol for 11 strategies, linear. At
  1000 symbols that is 340 s, **38 % of a 900 s (15m) bar**.
- **Network wall — `fetch@3/s`:** one candle request per symbol (candles
  **cannot** be batched on SmartAPI — see the architecture doc's audit). At
  1000 symbols that is 333 s, **37 % of a 15m bar**.

Everything else is noise by comparison: the scheduler is a warm-cache lookup
after startup, store writes are paid only on new bars, the scanner and risk are
milliseconds, and the dashboard is a few hundred milliseconds regardless.

```
 1000 symbols, 15m bar (900 s budget):

 scan+strat  ████████████████████████████████████████         340 s  (38%)
 fetch@3/s   ██████████████████████████████████████           333 s  (37%)
 store       ██                                                 21 s   (2%)
 provider    █                                                  10 s   (1%)
 scheduler   ▌                                                   5 s   (<1%, once)
 dashboard   ▏                                                 0.4 s
```

CPU and network are each ~37 % of the bar. They **overlap** (fetch is
I/O-bound, scan is CPU-bound), but running them back-to-back at 1000 symbols
leaves little margin — so **~250 symbols is comfortable at 15m and ~400 is the
practical ceiling**, unchanged from v1's verdict but now with the per-stage
evidence for *why*.

### What v2 changed

The candle fetch wall is inherent to the provider (one request per symbol per
bar) and v2 does not pretend otherwise. What v2 changed:

1. **Only due symbols, incrementally.** The store column is paid only for
   symbols that got a new bar; v1 re-read and re-processed the whole watchlist
   every cycle. Steady-state cost is one request per symbol per **bar**, not
   per **cycle**.
2. **Quotes batched 50:1.** Position marking left this table entirely — it is
   bounded by open positions, not universe size, and costs one request per 50
   symbols on an independent budget. v1 issued one `ltpData` per symbol.
3. **The fetch wall is explicit.** `check_budget()` computes requests/hour vs
   the 5000/hour cap at startup and refuses to let the operator discover an
   over-capacity universe at 12:30.
4. **One poller, not three.** No duplicate fetches, no competing timers.

---

## v1 — universe size vs candle interval (Phase 6, retained)

_Measured 2026-07-20 against the old polling feed. 11 strategies on 15m, 200
bars per symbol._

| symbols | data fetch (s) | scan + evaluate (s) | CPU (s) | peak MB | ms/symbol |
|--------:|---------------:|--------------------:|--------:|--------:|----------:|
|     100 |           4.26 |               32.03 |    5.30 |     1.8 |       320 |
|     250 |          10.90 |               83.57 |   12.73 |     3.4 |       334 |
|     500 |          21.91 |              171.53 |   26.38 |     6.3 |       343 |
|    1000 |          43.16 |              329.57 |   50.42 |    11.7 |       330 |

Scaling is **linear** — ~330 ms/symbol for 11 strategies. The v2 `scan+strat`
column reproduces this (it is the same strategy code); v2's contribution is the
per-stage split that isolates it from the market-data cost.

### The binding constraint (v1 analysis, confirmed by v2)

`SmartApiDataProvider` fetches one incremental request per symbol per bar, and
candles cannot be batched. At ~3 req/s that is roughly `symbols / 3` seconds of
wall time per bar:

| symbols | fetch @3/s | + scan | % of a 15m bar | verdict |
|--------:|-----------:|-------:|---------------:|---------|
|     100 |       33 s |  68 s |             8 % | comfortable |
|     250 |       83 s | 172 s |            19 % | comfortable |
|     500 |      167 s | 343 s |            38 % | tight if serialised |
|    1000 |      333 s | 673 s |            75 % | ceiling |

### Raising the ceiling (in order of value)

1. **Trade a longer candle.** A 1h bar gives 3600 s, making 1000 symbols
   comfortable.
2. **Add a second provider** (Zerodha) on the same `MarketDataSource` seam —
   changes the slope, not just the constant.
3. **Tier the cadence.** Fetch near-signal and held symbols every bar; fetch
   the cold tail less often.

None of these touch the scanner. See `docs/MARKET_DATA_ARCHITECTURE_V2.md` §12.

---

## Relationship to the current watchlist (§13)

The engine currently scans 99 symbols — comfortably inside every limit above,
and **constrained by data, not performance**: of 745 candidate symbols, only 99
have 15m history in the store. Widening the universe is a data-download task
first; the benchmark says the engine will absorb ~250 symbols without strain and
~400 at the limit.
