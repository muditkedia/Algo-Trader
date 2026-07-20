# DATA ACQUISITION REPORT

_Written 2026-07-19 after the Phase-2 acquisition run (context series + 15m
backfill + 5m history) and the full store audit._

## ⚠ Gate verdict: **CONDITIONS NOT MET — STOPPED**

The instruction was to proceed to shared-feature implementation only if all
downloads completed successfully, all validation checks passed, and the audit
finished without unresolved integrity issues. **None of the three holds
fully.** Per instruction, no feature work was started; this report documents
the failures and the remediation options. Everything acquired is stored,
manifest-logged, and audited; nothing was committed.

**What failed (4 payloads, 3 symbols):**

| Payload | Status | Diagnosis (re-fetched read-only and measured) |
|---|---|---|
| **NIFTY50 / 15m** (2015→now) | **QUARANTINED — series absent** | 74 impossible bars of 71,145 (0.104%, vs the 0.1% tolerance line) clustered in 2017 (35), 2018 (13), 2020 (26) — provider-side index-calculation glitches (high<low etc.) |
| **GAIL / 15m** head backfill (2016-10→2022) | **QUARANTINED — still starts 2023-01** | 189 bad of 38,554 (0.49%), clustered 2021 (121) + 2022 (64) — around GAIL's bonus/split era |
| **GAIL / 5m** (2016-10→now) | **QUARANTINED — no 5m series at all** | 293 bad of 181,110 (0.16%), same clusters |
| **PFC / 15m** head backfill | **QUARANTINED — still starts 2023-01** | 41 bad of 38,535 (0.106% — just over the line), clustered 2022 (36). PFC's **5m PASSED** (glitches are interval-specific) |

The quality gate did exactly what it was designed to do: these payloads'
glitch-bar rates exceed the `max(5 rows, 0.1%)` tolerance calibrated on
2023+ feeds. Pre-2023 SmartAPI data is simply glitchier (~0.1–0.5% vs
0.005% observed on 2023+ data).

## 1. What was acquired successfully

**Storage: 119 MB → 578 MB** (5m: +370 MB new; 15m: 41→149 MB; context in
1d; `manifest.json` now records every operation).

| Timeframe | Symbols | Bars | Coverage |
|---|---|---|---|
| 5m *(new)* | 101 = 98 equities + NIFTY50 + BANKNIFTY + INDIAVIX | 17.59 M | equities 2016-10-03→now (83 full-depth; 14 later = genuine IPOs; **GAIL absent**) |
| 15m | 101 = 99 equities + BANKNIFTY + INDIAVIX (**NIFTY50 absent**) | 5.78 M | 97 equities back to 2016-10-03; **GAIL, PFC still 2023-01+** |
| 1h | 99 equities (unchanged) | 0.60 M | 2023-01→now |
| 1d | 503 = 500 equities + 3 context | 1.13 M | 2015→now |

**Context series detail:**

| Series | 1d | 15m | 5m |
|---|---|---|---|
| NIFTY50 | ✔ 2015-01→now (2,846 rows; one 12-session hole Aug–Sep 2017, see §3) | **✗ quarantined** | ✔ 2015-01→now (213,269 rows; 118 glitch bars dropped, logged) |
| BANKNIFTY | ✔ 2015-01→now | ✔ 2015-01→now (71,140) | ✔ 2015-01→now (213,375) |
| INDIAVIX | ✔ 2015-01→now | ✔ **2015-12**→now (65,477; provider's VIX-intraday depth) | ✔ 2015-12→now (196,383) |

## 2. Provider limitations (measured by probe, not assumed)

- **Equity intraday depth: 2016-10-03** for both 15m and 5m (requests for
  2015–2016-09 return empty). Index intraday reaches **2015-01-01**
  (probably further; not probed deeper — no consumer). **VIX intraday
  begins 2015-12-01.**
- **Equity 1m exists from at least 2017-01** (documented only — not in the
  approved acquisition list; not downloaded in bulk).
- **India VIX intraday IS available** (the "if available" question is
  resolved: yes, 15m and 5m).
- Index/VIX candles carry volume=0 throughout (expected; passes the quality
  gate by design).
- Pre-2023 payloads carry 20–100× more glitch bars than 2023+ payloads —
  the binding limitation behind all four failures.
- SmartAPI candles are **unadjusted** for corporate actions; the GAIL/PFC
  glitch clusters sit around bonus/split dates. (Adjustment remains
  explicitly out of scope per the approved plan.)

## 3. Audit summary (`scripts/store_audit.py` — full detail in
`user_data/backtest_results/reports/store_audit.md`)

Verdict: **FAIL** (hence the gate). Findings by class:

**(a) Acquisition failures** — §0 above (the NIFTY50-15m absence also
surfaces as `5m/15m` context-completeness findings).

**(b) Provider-side sparse history (intraday, informational — not
re-downloadable):**
- **VBL**: 806 missing 15m sessions post-listing — the provider's VBL
  intraday history has a genuine hole: 2016 (38 sessions), 2017 full, 2018
  (46), **2019–2020 zero**, 2021 (141), 2022+ full.
- **SHRIRAMFIN**: 62 missing sessions in 2021 (data under this symbol is
  partial before the 2022 Shriram merger/rename).
- **BAJAJHLDNG / NAUKRI / VBL**: 23–39 sessions with <20 of 25 bars on
  normal days in the early era (thin trading — 15m windows with no prints).
- Impact: honest, name-specific early-era sparseness; downstream features
  must tolerate missing early sessions per symbol (already the design).

**(c) Pre-existing 1d-corpus findings (exposed by the new audit, NOT caused
by this acquisition):**
- **NIFTY50 1d has a 12-session hole 2017-08-28 → 2017-09-12 IST**
  (provider-side; the audit's UTC-normalized dates display one day earlier).
- Six NIFTY-500 names with multi-session 1d holes (FORCEMOT 77, GALLANTT
  98, PATANJALI 70, RECLTD 17, SPLPETRO 33, TMPV 11) — suspensions,
  relistings and renames in the 2015–2026 record (market reality, kept
  as-is).

**(d) Benign / by-design:**
- 17 market-wide special sessions self-identified across 2015–2026
  (Muhurat evenings, the 2024 NSE Saturday drills, budget-day Saturdays) —
  the automatic classification worked as designed.
- Per-symbol warnings of ≤5 missing sessions trace to the known Muhurat
  date-stamp variants (e.g. 2022-10-23/24, 2023-11-11/12) — harmless.
- 1h carries a "no NIFTY50 context" warning: 1h context was deliberately
  not acquired (15m context serves 1h consumers by as-of join, per plan).

## 4. Validation summary

| Check | Result |
|---|---|
| Downloads: context series | **Partial** — 8 of 9 series OK; NIFTY50/15m quarantined |
| Downloads: equity 15m backfill | **Partial** — 97/99 to full depth; GAIL, PFC rejected |
| Downloads: equity 5m | **Partial** — 98/99; GAIL rejected |
| Ingest integrity (duplicates, ordering, OHLC sanity) | **PASS** — zero duplicate/ordering failures across 804 series; isolated glitches dropped loudly (≤0.06% per admitted payload, all logged + manifest-recorded) |
| Session-calendar integrity | **PASS with documented exceptions** — classes (b)/(c) above |
| Context completeness | **FAIL** — NIFTY50 missing at 15m (the primary strategy timeframe) |
| Manifest | **PASS** — present, every operation logged |
| Existing tests | **PASS** — 413 passed, 1 skipped (nothing outside the data layer touched) |

## 5. Remediation options (owner decision required — none executed)

1. **Resample NIFTY50 15m from its clean 5m series** (and PFC's 15m head
   from PFC's clean 5m). Pure local derivation — no tolerance change, no new
   download; manifest records `op: resampled_from_5m`. Bar boundaries align
   exactly (three 5m bars per 15m bar). *Recommended for NIFTY50 + PFC.*
2. **Tolerance override for the failed payloads**: expose the
   IngestionEngine's existing configurable tolerance
   (`max_invalid_row_pct` / `max_invalid_rows`) as download-script flags and
   re-ingest ONLY the four payloads at e.g. 0.5% — the glitch bars are then
   dropped loudly (74/189/41/293 rows) exactly as the engine's design
   intends for isolated glitches. This is a quality-policy call, hence not
   taken unilaterally. *Required for GAIL (its 5m failed too, so option 1
   cannot cover it).* Chunked re-ingestion was evaluated and rejected: the
   glitches cluster (GAIL 2021: 121 bad in one year ≈ 1.9%), so smaller
   windows quarantine even harder.
3. **Accept the gaps**: NIFTY50 alignment features fall back to the 5m
   context (as-of joined to 15m bars) without any store change; GAIL/PFC
   simply have shorter intraday history (2 of 99 names). Zero work, honest,
   but leaves the store asymmetric.

My recommendation: **1 + 2-for-GAIL** (smallest policy surface, fixes the
context series exactly, keeps every drop logged), then re-run
`store_audit.py` and update this report. Classes (b)/(c) are documented
reality, not fixable by re-downloading.

---

# Addendum — microcap-tier 5m acquisition (2026-07-19, later phase)

_Owner decision taken before this run: data-quality remediation work is
STOPPED; the store as-is is the working baseline. This acquisition expanded
the dataset only, using the unchanged pipeline and its normal ingestion
validation._

**Universe:** the official **NIFTY Microcap 250** constituent list
(downloaded 2026-07-19 from NSE archives — ranks ~501–750 by market cap;
`microcap250.txt`, 245 EQ-series symbols after filtering, all resolvable in
the SmartAPI master, zero overlap with the NIFTY-500 snapshot). **Ranks
751–1000 have no official machine-readable NSE source** (no index exists
beyond the 750-name Total Market; the monthly MCAP archive is not
retrievable here) — documented limitation, tier approximated by the official
501–750 band.

**Acquired: 5-minute candles, 2016-10-01 → 2026-07-17** (the provider's
maximum equity-intraday depth, measured earlier):

| Metric | Value |
|---|---|
| Symbols acquired | **242 / 245** (JAYNECOIND recovered on a resume re-run after a transient read timeout) |
| Rows added | **~28.96 M** (5m store 370 MB → 899 MB; whole store now ~1.11 GB) |
| Full-depth (2016-10) symbols | 125 |
| Later starts (genuine listings) | 117 — IPO-year spread 2016–2026, incl. 25 × 2025 listings; kept as-is per instruction |
| Quality-quarantined (excluded) | **DIACABS, SKYGOLD, V2RETAIL** (glitch-bar rates over the standard tolerance — kept out by normal validation; no remediation attempted per the stop-work decision) |
| Isolated glitch bars dropped (within tolerance, logged) | 48 symbols, single-digit bars each |
| Manifest | all operations recorded in `user_data/data/nse/manifest.json` |

No new audit work was performed beyond the pipeline's normal ingestion
validation, per instruction. Expect microcap-tier data to be materially
sparser/noisier than the NIFTY-100 tier (thin sessions, wide spreads); any
future strategy consuming this tier should treat that as a property of the
universe, not a defect.
