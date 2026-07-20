# DATA INFRASTRUCTURE PLAN

_Written 2026-07-19. Planning and architecture only — nothing was downloaded,
no code was changed. Companion documents: `MARKET_CONTEXT_FEATURES.md`,
`STRATEGY_DEPENDENCY_MATRIX.md`, `IMPLEMENTATION_SEQUENCE.md`._

## 1. Audit — what the data architecture is today

**Pipeline (all existing, all reused by this plan):**
`scripts/download_history.py` → `SmartApiDataProvider` → `IngestionEngine`
(quality gate) → `MarketDataStore` (parquet).

| Layer | File | Verified behaviour relevant to this plan |
|---|---|---|
| Store | `src/algo/data/store.py` | Layout `<root>/<timeframe>/<SYMBOL>.parquet`; writes are timestamp-keyed **upserts** (merge + drop duplicate timestamps, last wins) — idempotent re-ingestion; discovery is directory-driven (`timeframes()`, `symbols(tf)`), so **a new timeframe is just a new directory** |
| Ingestion | `src/algo/data/ingest.py` | `incremental_update` fetches only the missing tail AND backfills **head gaps** when a requested start predates stored coverage (D-029, status `backfilled`) — the 2016 extension needs no new logic; per-symbol failures never abort a batch; bad feeds are **quarantined loudly** |
| Quality | `src/algo/data/quality.py` | Blocking errors (invalid OHLC, NaN, negative volume, duplicates) vs warnings; isolated glitch bars are dropped under a counted tolerance `max(5 rows, 0.1%)`; **volume == 0 is legal** (only `volume < 0` errors) — index/VIX series, which carry no traded volume, pass unchanged |
| Provider | `src/algo/data/providers/smartapi/provider.py` | Interval map already covers `1m/3m/5m/10m/15m/30m/1h/1d` with documented per-request day caps (1m: 30, 5m: 100, 15m: 200, 1h: 400, 1d: 2000); throttled (1 req/s conservative), rate-limit backoff, token-refresh retry; `fetch_ohlcv(symbol…)` resolves `instruments.token_for(symbol)` and everything downstream is symbol-agnostic |
| Instruments | `src/algo/data/providers/smartapi/instruments.py` | Official scrip master, currently filtered to `exch_seg=="NSE"` **and `-EQ` series only** — this is the ONE place indices are invisible today |
| Audit tooling | (scratchpad only) | The session-gap audit used for `DATA_CAPABILITY_REPORT.md` exists only as a throwaway script — formalized below |

**Current holdings:** 15m/1h × 99 NIFTY-100 (2023-01→2026-07, clean; 878
sessions), 1d × 500 NIFTY-500 (2015→now). No 1m, no 5m, no index, no VIX.

## 2. Required capabilities → design deltas

| Requirement | Design delta | Code impact |
|---|---|---|
| NIFTY 50 / Bank NIFTY intraday | Index token resolution in the instrument layer + canonical names (§3); provider/store/ingest unchanged | One additive method + a 3-entry mapping |
| India VIX intraday *(if available)* | Same mechanism; availability must be **probed** (SmartAPI serves index candles for NIFTY/BANKNIFTY reliably; VIX intraday candle support is unverified — fall back to 1d VIX if the probe fails) | None beyond the above |
| 5-minute candles | New store directory `5m/`; provider already maps `5m` | **Zero** |
| 1-minute candles *(if available)* | New store directory `1m/`; provider already maps `1m`; depth must be probed | **Zero** |
| History → ~2016 | `incremental_update(..., start_if_empty=2016-01-01)` triggers the existing head-gap backfill per symbol × timeframe | **Zero** |

## 3. Storage layout, folder structure, naming conventions

**Decision: one store, unchanged layout.** Market-context series live in the
SAME `user_data/data/nse/<timeframe>/` tree under **reserved canonical
symbols**:

```
user_data/data/nse/
  1m/   RELIANCE.parquet … NIFTY50.parquet BANKNIFTY.parquet            (new dir)
  5m/   RELIANCE.parquet … NIFTY50.parquet BANKNIFTY.parquet INDIAVIX.parquet   (new dir)
  15m/  RELIANCE.parquet … NIFTY50.parquet BANKNIFTY.parquet INDIAVIX.parquet   (existing dir, new symbols + 2016 backfill)
  1h/   (existing; can be resampled from 15m if deeper history is ever needed)
  1d/   … NIFTY50.parquet BANKNIFTY.parquet INDIAVIX.parquet            (existing dir, new symbols)
  _instruments/  (existing master cache; gains the index cache file)
  manifest.json  (new - §7)
```

**Naming conventions:**
- Canonical context symbols: `NIFTY50`, `BANKNIFTY`, `INDIAVIX` — mapped from
  the scrip-master names ("Nifty 50", "Nifty Bank", "India VIX";
  `instrumenttype == "AMXIDX"`). Tokens are **discovered from the master at
  runtime**, never hardcoded (well-known values like 99926000/99926009 are
  used only as cross-checks in tests).
- No collision risk: NSE cash tickers cannot equal these names, and context
  symbols are **never listed in universe files** (`nifty100.txt` …), so
  scanners/backtests can never trade them. Rule: *universe files enumerate
  tradeables; context symbols are reachable only by explicit name.*
- Timeframe directory names stay platform-style: `1m`, `5m`, `15m`, `1h`, `1d`.

**Rationale for "same store" (vs a separate index store):** every consumer —
research engine, scanner, backtest runners, the future MarketContext builder —
already takes `store.read(symbol, timeframe)`. A second store root would add a
second handle through every constructor for zero benefit.

## 4. Update pipeline

Reuse `scripts/download_history.py` unchanged for equities. Add one thin
wrapper (or a `--context` flag) whose only job is symbol selection:

- `--context` resolves {NIFTY50, BANKNIFTY, INDIAVIX} via the extended
  instrument lookup and runs the SAME `incremental_update` per timeframe.
- Everything else (chunking, throttle, retry, quality, quarantine, upsert)
  is the existing path.

**Planned invocations (for the later, approved execution — with estimates at
the conservative 1 req/s throttle):**

| Step | Invocation (conceptual) | Requests | Wall clock | Store growth |
|---|---|---|---|---|
| 0 Probe | 1 request per (interval × era: 2016/2017/2019) for one equity + NIFTY50 + INDIAVIX | ~20 | minutes | — |
| 1 Context series | context symbols × {1d, 15m, 5m} from 2016 | ~60–80 | <10 min | ~10–30 MB |
| 2 15m backfill → 2016 | 99 equities × ~13 chunks | ~1.3k | ~25–40 min | 119 MB → ~350 MB |
| 3 5m 2016→now | 99 equities × ~39 chunks | ~3.9k | ~1–1.5 h | ~1 GB |
| 4 (opt) 1m subset | ~20 liquid names × ~128 chunks | ~2.6k | ~45 min | ~1–2 GB |

Each step is resumable (re-run = no-op) and ends with the store audit (§6).

## 5. Incremental refresh & backfill logic (existing semantics, restated)

- **Tail refresh:** `incremental_update` fetches from `last_date + 1 bar` to
  now; up-to-date symbols are no-ops. This is the daily/production top-up for
  ALL timeframes including context symbols.
- **Head backfill:** passing `start_if_empty=2016-01-01` backfills any symbol
  whose stored coverage starts later (status `backfilled`, loud). Symbols
  listed after 2016 simply return their true listing start — no special
  casing (the JIOFIN precedent).
- **Idempotency:** window arithmetic never re-requests stored bars, and the
  store upserts by timestamp — the two-layer duplicate protection already
  proven in Phase 7.

## 6. Integrity: validation, duplicate detection, gap detection

**Already enforced at ingest:** OHLC sanity, NaN, negative volume, duplicate
timestamps, monotonic ordering; isolated-glitch drop tolerance
(counted + logged); whole-feed quarantine. Index/VIX note: zero volume is
valid (§1); no gate change needed.

**New (to be built as part of this plan's execution): `scripts/store_audit.py`**
— the formalization of the audit used for `DATA_CAPABILITY_REPORT.md`, run
after every bulk operation and on demand:

- **Session-calendar check:** union of session dates across symbols per
  timeframe; per-symbol missing sessions AFTER its own listing start.
- **Bars-per-session profile:** expected 25 (15m), 75 (5m), 375 (1m), 7 (1h)
  for normal sessions; a maintained **special-session whitelist** (Muhurat
  days, the 2024 NSE Saturday DR sessions, any future half-days) so genuine
  short sessions don't raise false alarms. Known store quirk to encode: the
  2023 Muhurat session is date-stamped 2023-11-11 by 3 symbols and
  2023-11-12 by 96.
- **Cross-timeframe consistency:** 5m sessions ⊆ 15m sessions per symbol;
  15m daily aggregates ≈ the 1d bar (tolerance for auction prints).
- **Context-series completeness:** NIFTY50/BANKNIFTY present for every
  equity session (the market features fail loudly otherwise).
- Output: a written report under `user_data/backtest_results/reports/` plus a
  non-zero exit code on hard failures — usable as a gate in any later
  automation.

## 7. Data versioning

Parquet files stay unversioned (the store's `normalize()` contract is the
schema). Versioning is **manifest-based**: a `manifest.json` at the store
root, updated by the download wrapper from each `IngestionReport`:

```json
{"NIFTY50": {"15m": {"first": "...", "last": "...", "rows": 0,
              "last_update": "...", "events": [
                {"ts": "...", "op": "backfilled", "added": 123,
                 "dropped_invalid": 0, "provider": "smartapi"}]}}}
```

- Every bulk operation appends an event (op, window, rows added, rows
  dropped, quarantines) — the store's history becomes inspectable without
  git-tracking gigabytes.
- Dated universe snapshots continue as today (`nifty500.txt` committed with
  its date — the D-032 precedent).
- Quarantined payloads keep going to the existing quarantine path, never the
  store.

## 8. Compatibility with the existing strategy engine

- **Strategies:** zero changes required by the data work. They keep receiving
  their own symbol's frame via `store.read` → `prepare`. New timeframes and
  new symbols are invisible until something asks for them.
- **Feature delivery (design, detailed in `MARKET_CONTEXT_FEATURES.md`):**
  market-context columns (`mkt_*`, `vix_*`) are stamped onto per-symbol
  frames by the RUNNER/SCANNER layer before `prepare`, from context frames
  read out of this same store. The frozen research engine and the execution
  engine are untouched; strategies opt in per-column during their own later
  upgrade phase.
- **Paper engine:** its rolling top-up path (`incremental_update` without a
  start) already covers context symbols the moment they are added to its
  refresh list — no engine change.
- **Research path:** completely unaffected (reads the same store; recorded
  verdicts reproduce because existing files are only ever EXTENDED backward/
  forward, and any rerun of a frozen report pins its own date window).

## 9. Risks & unknowns (to resolve in the probe step, before any bulk work)

1. **Server-side history depth per interval** is documented ≈2016 but not
   verified — the probe measures actual first-available bars per interval.
2. **India VIX intraday availability** via `getCandleData` is unverified;
   fallback is VIX 1d (still sufficient for the volatility-regime feature).
3. **Pre-2023 data quality** is unknown (splits/bonuses appear as price
   jumps; SmartAPI candles are unadjusted) — the audit's cross-timeframe and
   gap checks plus the quality gate quantify this before anything consumes
   it; corporate-action adjustment is explicitly OUT of scope here and would
   be its own decision.
4. **Request budget:** ~8k requests total for steps 1–3 — well within a
   session at 1 req/s, but run off-hours; the throttle/backoff already
   handles enforcement stricter than documented.
5. Symbols that entered NIFTY-100 after 2016 will have shorter (honest)
   backfills; the audit reports per-symbol starts so nobody mistakes listing
   dates for gaps.

## 10. Acceptance criteria (for the later execution phase)

- Probe report written; depths per interval known; VIX path decided.
- Context series present at 1d/15m (+5m where available) with audit-clean
  sessions matching the equity calendar.
- 15m/5m coverage back to the verified maximum for all 99 symbols
  (post-listing), audit-clean; manifest events recorded for every operation.
- `store_audit.py` green; all existing tests green; **no strategy, engine,
  or result file modified by the data work.**
