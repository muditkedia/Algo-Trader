# STRATEGY DEPENDENCY MATRIX

_Written 2026-07-19. Design only. Feature definitions: F1–F16 in
`MARKET_CONTEXT_FEATURES.md`. Markings reflect the practitioner forms
documented in `IMPLEMENTATION_GUIDE.md`:_
**R** = Required (the practitioner form gates on it) ·
**Rec** = Recommended (consistently advised context) ·
**O** = Optional · **—** = Not used.

| Feature → | F1 clock | F2 RVOL | F3 gap | F4 sess type | F5/F6 index align | F7 VWAP slope | F8 VWAP tests | F9 OR width | F10 CPR class | F11 ATR regime | F12 BBW pct | F13 trend str | F14 VIX regime | F15 turnover | F16 events |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `orb_5m` | **R** | **R** | **R** | Rec | **R** | — | — | **R** | — | **R** | — | **R** | Rec | **R** | Rec |
| `vwap_15m` | **R** | Rec | Rec | Rec | **R** | **R** | **R** | — | — | O | — | Rec | O | O | Rec |
| `vwap_pullback_15m` | **R** | Rec | Rec | Rec | **R** | **R** | **R** | — | — | O | — | O | O | O | Rec |
| `cpr_breakout_15m` | **R** | **R** | **R** | Rec | **R** | — | — | O | **R** | Rec | — | — | Rec | O | Rec |
| `orb_retest_5m` | **R** | **R** | **R** | Rec | **R** | — | — | **R** | — | **R** | — | **R** | O | **R** | Rec |
| `opening_drive_5m` | **R** | **R** | **R** | Rec | **R** | — | — | — | — | **R** | — | — | O | **R** | Rec |
| `gapgo_5m` | **R** | **R** | **R** | Rec | Rec | — | — | **R** | — | **R** | — | O | O | **R** | Rec |
| `pullback_15m` | **R** | Rec | O | Rec | **R** | — | — | — | — | O | — | **R** | O | O | Rec |
| `volexp_1h` | **R** | Rec | O | Rec | Rec | — | — | — | — | Rec | **R** | Rec | Rec | O | Rec |

## Reuse counts (build-priority signal)

| Feature | R | Rec | O | Serves (R+Rec) | Unblocked today? |
|---|---|---|---|---|---|
| F1 session clock | 7 | 0 | 0 | **7** | Yes |
| F5/F6 index alignment | 6 | 1 | 0 | **7** | No — needs NIFTY50 series (data step 1) |
| F4 session type | 0 | 7 | 0 | **7** | Yes (expiry flag needs the small CSV) |
| F16 event flag | 0 | 7 | 0 | **7** | No — needs a calendar source |
| F2 RVOL (time-of-day) | 4 | 3 | 0 | **7** | Yes |
| F3 gap classification | 2 | 4 | 1 | **6** | Yes |
| F11 ATR regime | 0 | 4 | 3 | **4** | Yes |
| F13 trend strength | 1 | 3 | 2 | **4** | Yes |
| F14 VIX regime | 0 | 3 | 4 | **3** | No — needs INDIAVIX series |
| F7 VWAP slope | 2 | 0 | 0 | **2** | Yes |
| F8 VWAP test counter | 2 | 0 | 0 | **2** | Yes |
| F9 OR width | 1 | 1 | 1 | **2** | Yes |
| F10 CPR classification | 1 | 0 | 0 | **1** (method-defining) | Yes |
| F12 BBW percentile | 1 | 0 | 0 | **1** (definitional; reusable by future squeeze variants) | Yes |
| F15 turnover | 0 | 0 | 7 | 0 | Yes |

**Reading the matrix:**
- Three features serve all seven strategies and are unblocked today —
  **F1, F2, F4** — plus **F3** at six: these four are the first build.
- The only R-grade features that are data-blocked are **F5/F6** (index
  series) — the strongest argument for data step 1 being the first
  download when data work is approved. **F14** rides on the same step.
- **F16** (events) is Recommended everywhere but has no source; it stays a
  data-acquisition decision, not a feature-engineering task.
- Single-strategy features (**F10**, **F12**) are still shared code:
  they encode definitional standards future strategies will reuse
  (CPR reversal → F10; any squeeze variant → F12).
- Nothing in this matrix creates a per-strategy calculation: every mark is a
  call into `algo/features/` helpers, computed once per frame/run.
