# HISTORICAL DATA CAPABILITY REPORT

_Updated 2026-07-19 after the Phase-2 acquisition (original audit-only
version: 2026-07-19 morning). Full acquisition detail, failures and
remediation options: `docs/DATA_ACQUISITION_REPORT.md`._

## 1. What intraday data exists now (`user_data/data/nse/`)

| Timeframe | Symbols | Date range | Bars | Notes |
|---|---|---|---|---|
| **1m** | — | — | — | Not stored. Provider exposes it from ≥2017 (probed); acquisition deferred by plan |
| **5m** | 101 (98 equities + NIFTY50 + BANKNIFTY + INDIAVIX) | equities 2016-10-03 → now; indices 2015-01 → now; VIX 2015-12 → now | 17.59 M | **NEW.** GAIL absent (quarantined — see acquisition report) |
| **15m** | 101 (99 equities + BANKNIFTY + INDIAVIX) | 97 equities 2016-10-03 → now; GAIL/PFC 2023-01 → now | 5.78 M | Backfilled ×3 history. **NIFTY50 absent (quarantined)** |
| **1h** | 99 equities | 2023-01 → now | 0.60 M | Unchanged (15m context serves 1h by as-of join) |
| **1d** | 503 (500 equities + 3 context) | 2015 → now | 1.13 M | Context series added |

Store size 578 MB (was 119 MB). `manifest.json` now logs every bulk
operation. Audit tooling: `scripts/store_audit.py` (+ `algo/data/audit.py`),
report at `user_data/backtest_results/reports/store_audit.md`.

**Index intraday data now exists** (BANKNIFTY 15m/5m from 2015; NIFTY50 5m
from 2015; India VIX 15m/5m from 2015-12 — VIX intraday availability is
confirmed, resolving the plan's open question). **NIFTY50 15m is the one
missing context series** (its payload was quarantined at 0.104% glitch bars
vs the 0.1% tolerance; remediation options in the acquisition report).

## 2. Known data caveats (measured)

- **Provider depth:** equity intraday begins 2016-10-03 (both 15m and 5m);
  index intraday ≥2015-01-01; VIX intraday 2015-12-01; equity 1m ≥2017.
- **Glitch-bar rates:** 2023+ payloads ~0.005%; 2016–2022 payloads
  0.05–0.5% (all admitted payloads had their isolated glitches dropped
  loudly; four payloads exceeded tolerance and were quarantined whole —
  NIFTY50/15m, GAIL/15m+5m, PFC/15m).
- **Name-specific sparse history (provider-side, not re-downloadable):**
  VBL (no intraday 2019–2020 and thin 2016/2018/2021), SHRIRAMFIN (partial
  2021, pre-merger), BAJAJHLDNG/NAUKRI (dozens of thin early-era sessions).
- **Pre-existing 1d-corpus holes** (exposed by the new audit): NIFTY50 1d
  misses 12 sessions (2017-08-28→09-12 IST); six NIFTY-500 names carry
  suspension/relisting holes (FORCEMOT, GALLANTT, PATANJALI, RECLTD,
  SPLPETRO, TMPV).
- **Special sessions:** 17 market-wide short sessions (Muhurat evenings,
  2024 NSE Saturday drills, budget Saturdays) self-identified by the audit;
  Muhurat date-stamp variants across symbols are a known benign quirk.
- Candles are **unadjusted** for corporate actions (splits/bonuses appear
  as price jumps; adjustment is explicitly out of scope).

## 3. Assessment

The two structural gaps identified in the original report are now closed in
substance: **regime coverage** (intraday history spans 2016-10 → 2026-07:
the 2018 vol events, 2020 crash and recovery, 2021 bull, 2022 chop, and
2023–2026) and **market-context series** (index + VIX intraday exist). The
remaining blockers are narrow and enumerated: the NIFTY50 15m series
(quarantined; three remediation options stand ready for owner decision) and
two equities' pre-2023 intraday history (GAIL, PFC). Until the NIFTY50 15m
decision, index-alignment features can only be built against the clean
NIFTY50 5m series (as-of joined) — workable but not the intended layout.

Earnings/results calendar and sector map remain the two absent external
datasets (blocking event-avoidance and sector-alignment features
respectively), unchanged from the original report.
