# MARKET CONTEXT FEATURES — shared feature architecture

_Written 2026-07-19. Architecture design only — nothing implemented. The
strategy users referenced per feature come from `IMPLEMENTATION_GUIDE.md`;
the full Required/Recommended/Optional grid is
`STRATEGY_DEPENDENCY_MATRIX.md`._

## 0. Architecture

**New package (design): `src/algo/features/`** — practitioner-level,
reusable market-context computations, one level above the primitive math in
`core/indicators.py` (which stays the home of raw indicator series like
`atr`, `session_vwap`, `opening_range`). Layout:

```
src/algo/features/
  __init__.py
  symbol.py      # per-symbol causal transforms (need only that symbol's bars)
  market.py      # MarketContext: features from NIFTY50/BANKNIFTY/INDIAVIX +
                 # the session calendar (need context series from the store)
  join.py        # as-of stamping of market/context columns onto symbol frames
```

**Delivery seam (the one integration point):** the backtest runner and the
scanner build a `MarketContext` ONCE per run (reading `NIFTY50` / `INDIAVIX`
frames from the existing store) and stamp its columns (prefix `mkt_`,
`vix_`, `session_`) onto every symbol frame **before** `strategy.prepare` is
called. Per-symbol features are NOT stamped centrally — each strategy calls
the `features.symbol` helpers inside its own `prepare` when its upgrade phase
declares the corresponding rule (ownership preserved; no hidden platform
behaviour). Consequences:

- Strategies need **zero** changes until their own upgrade phase.
- The frozen research engine and the execution engine are untouched.
- No duplicated calculations: one helper per feature, one context build per
  run, columns computed once per frame.
- Causality rule for every feature: values at bar *t* use only data ≤ *t*
  (session-scoped cumulatives; prior-session aggregates shifted — the
  existing `central_pivot_range` pattern).

## 1. Feature catalog

Format — **Definition · Calculation · Inputs · Dependencies · Location ·
Users** (R = required by the practitioner form, per the dependency matrix).

**F1. Session clock & entry windows** (`session_clock`)
- Definition: per-bar session position: bar-of-session index, minutes since
  open, IST wall-clock, and boolean window flags (morning ≤ ~11:30, lunch
  12:00–13:30, late ≥ 14:30).
- Calculation: group bars by session date; cumcount; `date` converted to IST
  wall-clock; windows are pure clock comparisons.
- Inputs: the symbol's own `date` column. Dependencies: none.
- Location: `features/symbol.py`. Users: all seven (R) — every entry-window
  rule (roadmap A3) reads it.

**F2. Relative volume, time-of-day (RVOL)** (`rvol_tod`)
- Definition: this bar's volume relative to the average volume of the SAME
  bar-of-session over the trailing N sessions (default 20) — the metric
  practitioners mean by "1.5× volume".
- Calculation: for bar-of-session k on day d: `volume / mean(volume at k over
  the prior N sessions)` (prior sessions only — causal; NaN until enough
  history; short/special sessions excluded from baselines via F4).
- Inputs: volume + F1. Dependencies: F1, F4.
- Location: `features/symbol.py`. Users: orb (R), cpr (R), first_pullback
  (R), vwap_pullback (Rec), vwap_15m (Rec), volexp (Rec), pullback (Rec).

**F3. Opening gap classification** (`gap_class`)
- Definition: today's open vs the prior session close, signed, bucketed:
  flat (<0.3%), small (0.3–1%), moderate (1–2%), large (>2%) — the buckets
  practitioners actually reason with (thresholds are declared constants, not
  tunables).
- Calculation: first bar's open / prior session's last close − 1 (prior
  session aggregate, shifted — CPR pattern); carried on every bar of the day.
- Inputs: the symbol's own bars. Dependencies: none.
- Location: `features/symbol.py`. Users: orb (R), cpr (R), vwap pair (Rec),
  first_pullback (Rec), others (Opt).

**F4. Session type** (`session_type`)
- Definition: normal / special-short (Muhurat, DR drills) / truncated;
  plus an `expiry_day` flag (index derivatives expiry).
- Calculation: bars-per-session vs expected count + the maintained
  special-session whitelist (data plan §6). Expiry days require a small
  maintained calendar table (expiry weekday conventions changed across
  2023–2025 — deriving them from rules would be wrong; a committed CSV of
  exchange-published expiry dates is the honest source).
- Inputs: session bar counts; expiry CSV. Dependencies: whitelist/CSV.
- Location: `features/market.py` (session-level, market-wide). Users: all
  (Rec) — baselines (F2) and entry windows consult it.

**F5. Market trend state** (`mkt_trend`)
- Definition: the index tape's intraday state: up (NIFTY50 above its rising
  session VWAP), down (below falling), neutral otherwise; plus the index
  opening-range direction once formed.
- Calculation: `session_vwap` + slope over k bars + OR break direction, on
  the NIFTY50 intraday frame (BANKNIFTY available for bank-sector names
  later).
- Inputs: NIFTY50 (BANKNIFTY) intraday frames. Dependencies: **data step 1**
  (context series); reuses `core/indicators` primitives.
- Location: `features/market.py`; stamped as `mkt_trend`, `mkt_above_vwap`,
  `mkt_or_dir`. Users: all seven (R for the aligned practitioner forms).

**F6. Index alignment** (`index_aligned`)
- Definition: boolean — is a LONG entry consistent with F5 right now
  (long-only platform: `mkt_trend != down`, strict form `== up`).
- Calculation: trivial derivation of F5; strict/lenient variant is part of
  each strategy's declared rule, not a platform default.
- Inputs/Dependencies: F5. Location: `features/market.py` (+ `join.py`).
- Users: all seven (R/Rec per matrix).

**F7. VWAP slope (per symbol)** (`vwap_slope`)
- Definition: the symbol's session-VWAP slope state (rising/flat/falling)
  measured over k bars, normalized by ATR so "flat" is price-scale-free.
- Calculation: `(vwap_t − vwap_{t−k}) / atr_t` with a small flat band.
- Inputs: symbol bars. Dependencies: `session_vwap`, `atr` primitives.
- Location: `features/symbol.py`. Users: vwap_15m (R), vwap_pullback (R),
  others (—).

**F8. VWAP test counter** (`vwap_test_count`)
- Definition: how many times this session price has tagged/reclaimed VWAP so
  far — the "first/second test only" practitioner rule needs it.
- Calculation: session-scoped cumulative count of tag events (low touches
  VWAP band while close holds) and of reclaim events (close crosses VWAP
  upward), counted on PRIOR bars (shifted — the current bar's own event is
  the entry, not history).
- Inputs: symbol bars. Dependencies: `session_vwap`.
- Location: `features/symbol.py`. Users: vwap_pullback (R), vwap_15m (R).

**F9. Opening-range width metric** (`or_width_atr`)
- Definition: OR height as a fraction of the symbol's daily ATR — the
  practitioner "too wide to trade" test.
- Calculation: `(or_high − or_low) / daily_atr14` where daily ATR is the
  prior-session value (as-of, shifted).
- Inputs: symbol bars (intraday + its own daily aggregate). Dependencies:
  `opening_range`; a prior-sessions daily-ATR helper.
- Location: `features/symbol.py`. Users: orb (R), first_pullback (Rec),
  cpr (Opt).

**F10. CPR classification** (`cpr_class`)
- Definition: Ochoa day-typing: CPR width percentile vs its trailing ~20
  sessions (narrow/normal/wide), the two-day relationship (higher /
  overlapping-higher / inside / overlapping-lower / lower), and the
  virgin-CPR flag (prior CPR untouched by price).
- Calculation: from the existing `central_pivot_range` levels: width
  percentile over shifted trailing widths; relationship by comparing today's
  vs yesterday's range position; virgin flag via prior-session
  touched-or-not (all prior-session data only).
- Inputs: symbol bars. Dependencies: `central_pivot_range`.
- Location: `features/symbol.py`. Users: cpr (R); others (—).

**F11. ATR regime (per symbol)** (`atr_regime`)
- Definition: the symbol's daily ATR% (ATR/close) percentile vs its trailing
  year — is this name currently quiet or lively (breakout setups need
  liveliness).
- Calculation: daily ATR%(14) percentile over trailing ~250 sessions,
  shifted, stamped onto intraday bars per session.
- Inputs: symbol daily bars (or intraday-aggregated). Dependencies: `atr`.
- Location: `features/symbol.py`. Users: orb/cpr/volexp/first_pullback
  (Rec), others (Opt).

**F12. Bollinger bandwidth percentile** (`bbw_pct`)
- Definition: bandwidth relative to its OWN trailing distribution (Bollinger's
  actual squeeze definition), replacing absolute thresholds.
- Calculation: percentile rank of `bollinger_bandwidth` within its trailing
  M bars (shifted; M declared by the using strategy, e.g. ~120 1h bars).
- Inputs: symbol bars. Dependencies: `bollinger_bandwidth`.
- Location: `features/symbol.py`. Users: volexp_1h (R); squeeze_daily
  (future); others (—).

**F13. Trend strength (per symbol)** (`trend_strength`)
- Definition: intraday trend quality: fast/slow EMA separation in ATR units
  (with an optional structure check: count of higher lows).
- Calculation: `(ema_fast − ema_slow) / atr` + rolling higher-low count.
- Inputs: symbol bars. Dependencies: `ema`, `atr`.
- Location: `features/symbol.py`. Users: pullback_15m (R), vwap_15m (Rec),
  first_pullback (Rec), others (Opt).

**F14. Volatility regime (market)** (`vix_regime`)
- Definition: India VIX level and percentile band (low/normal/elevated/
  extreme) — the market-wide "is there anything to trade / is it too wild"
  context.
- Calculation: VIX close (1d, or intraday if the probe confirms
  availability) percentile vs trailing year, shifted; stamped per session.
- Inputs: INDIAVIX series. Dependencies: **data step 1**.
- Location: `features/market.py`. Users: orb/cpr/volexp (Rec), all (Opt).

**F15. Liquidity / turnover** (`turnover_median`)
- Definition: rolling median daily traded value — a floor guard even inside
  a curated universe.
- Calculation: median of `close × volume` daily aggregate over trailing ~20
  sessions, shifted.
- Inputs: symbol bars. Dependencies: none.
- Location: `features/symbol.py`. Users: all (Opt — the NIFTY-100 universe
  already enforces liquidity).

**F16. Event flag** (`event_day`) — *deferred (blocked on data)*
- Definition: symbol results day / major macro day (policy, budget, election
  results) — the practitioner no-trade day.
- Calculation: lookup against a maintained calendar. **No source is wired
  today** (SmartAPI candles cannot provide it) — requires its own
  acquisition decision (data plan §9/roadmap A11).
- Location: `features/market.py` when available. Users: all (Rec when
  available).

**Sector alignment** — *deferred (blocked on data)*: needs a sector map
(`instruments.sector` is empty for the whole universe) plus sectoral index
series. Listed for completeness; not designed further until that data
decision is taken.

## 2. Reuse summary (why these are platform features, not strategy code)

| Feature | Strategies using it (R+Rec) | Notes |
|---|---|---|
| F1 session clock | 7 | universal |
| F5/F6 market trend + alignment | 7 | needs context data |
| F2 RVOL | 7 | replaces the rolling-20 proxy *in gates* |
| F4 session type | 7 | baseline hygiene for F2 + windows |
| F3 gap class | 6 | breakout family core |
| F14 VIX regime | 3 (+4 Opt) | needs VIX series |
| F11 ATR regime | 4 | per-symbol liveliness |
| F13 trend strength | 3 | pullback family |
| F7 VWAP slope | 2 | VWAP pair |
| F8 VWAP test counter | 2 | VWAP pair (fixes over-firing) |
| F9 OR width | 2 (+1 Opt) | ORB family |
| F10 CPR class | 1 | but it IS the CPR method |
| F12 BBW percentile | 1 (+1 future) | definitional fidelity |
| F15 turnover | 0 R (7 Opt) | guard rail |
| F16 event flag | 7 Rec | blocked on source |

Features used by a single strategy (F10, F12) still live in `features/` —
they are definitional standards (Ochoa day-typing, Bollinger's squeeze) that
future strategies (CPR reversal, squeeze variants) will reuse.
