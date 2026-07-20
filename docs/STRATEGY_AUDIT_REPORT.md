# STRATEGY AUDIT REPORT

_Independent verification phase, 2026-07-19. Scope: all 12 implemented
intraday strategies (7 original + 5 batch-2) audited against their documented
canonical implementations (`docs/INTRADAY_STRATEGY_LIBRARY.md`,
`docs/IMPLEMENTATION_GUIDE.md`, `research/INTRADAY_PRODUCTION_BATCH1.md`,
module docstrings). Method: line-by-line code review against the documented
rules, causality re-derivation for every prepared column, crafted-session
tests, and quantification of suspicious behaviour on real store data._

## Audit verdict summary

| Strategy | Entry vs canon | Exits/stop/target | Session/EOD | Dup-entry / re-entry | Spec & param ownership | Verdict |
|---|---|---|---|---|---|---|
| orb_15m | ✔ (close-confirmed) | ✔ OR-low / 1×range | ✔ | **F3**: re-fires 8.9% of signal sessions vs documented "at most one" | ✔ | Pass w/ F3 |
| vwap_15m | ✔ | ✔ dip-low / 2R | ✔ | reclaim count uncapped — consistent with its "zero to two" doc; noted | ✔ | Pass |
| vwap_pullback_15m | ✔ | ✔ VWAP / 2R | ✔ | over-firing previously documented (5–9×); unchanged by design | ✔ | Pass |
| cpr_breakout_15m | ✔ | ✔ BC stop / R1 partial→BE→R2 | ✔ | **F3**: re-fires 8.2% | ✔ | Pass w/ F3 |
| first_pullback_15m | ✔ | ✔ pullback-low / 2R | ✔ | ✔ once/session enforced | ✔ | Pass |
| pullback_15m | ✔ | ✔ ATR/swing + chandelier (owned) | ✔ | re-fires possible (canonical for EMA pullbacks) | ✔ | Pass |
| volexp_1h | ✔ | ✔ ATR + chandelier (owned) | ✔ | ✔ squeeze gate self-limits | ✔ | Pass w/ F5 note |
| gapgo_15m | ✔ | ✔ first-bar-low / 2R | ✔ opening window | **F2 (FIXED)**: re-fired 11.2% vs its "one shot" doc | ✔ | Pass after fix |
| insidebar_15m | ✔ | ✔ inside-bar-low / 2R | ✔ same-session pattern enforced | multiple patterns/day = canonical ✔ | ✔ | Pass w/ F6 note |
| supertrend_15m | ✔ flip-triggered | ✔ line stop + line trail | ✔ | ✔ flips are inherently one-shot | ✔ | Pass w/ F9 note |
| cpr_reversal_15m | ✔ wide-gate + S1 reject | ✔ rejection-low / pivot target | ✔ | multiple tests/day = canonical for rotation ✔ | ✔ | Pass |
| nr7_intraday_15m | ✔ NR7 gate + prev-high break | ✔ prev-low / EOD ride | ✔ | **F1 (FIXED)**: re-fired 36.4% vs Crabel's single-breakout day-trade | ✔ | Pass after fix |

Verified for ALL 12: causality of every prepared column (prior-session
shifts, within-session prior-bar shifts, no future data at any bar);
ExecutionSpec ownership (declared in the owning module; enforced by test);
stop level validity at entry (structurally below entry or signal rejected);
EOD square-off + no-overnight (structural in the engine; 0 overnight trades
in the corrected full run); parameters frozen in each module's dataclass
next to its hypothesis; shared components used via `core/indicators` (no
duplicated math).

## Findings

**F1 — Critical→FIXED (classified Major): `nr7_intraday_15m` re-entered on
re-crosses of the narrow-day high.** 36.4% of its signal sessions fired
multiple same-day entries (10-symbol × 9.7y measurement); Crabel's canonical
day-trade is THE breakout — one trade per expansion day. Fixed with the
platform's once-per-session cap (the `first_pullback_15m` pattern);
regression test added. Signals on the 10-symbol sample: 2,553 → 1,626.

**F2 — Major→FIXED: `gapgo_15m` re-entered on re-crosses of the first-bar
high** in 11.2% of signal sessions, contradicting its own documented
behaviour ("at most one signal per stock per session, always early"). Same
fix + test. Signals: 89 → 80.

**F3 — Major, NOT fixed (owner decision required): `orb_15m` and
`cpr_breakout_15m` re-fire within a session** (8.9% / 8.2% of signal
sessions) while their metadata and the Phase-15 spec describe "at most one
long per stock per session". Their signal sets are pinned by recorded
research (D-026) and every recorded backtest; the canonical ENTRY rule as
written (close crosses level + volume) does not itself mandate once-per-
session. Changing the rule alters recorded strategy behaviour, so it belongs
to the owner-approved refinement phase (roadmap A3/A6 territory); the
descriptive metadata is inaccurate until then.

**F4 — Minor (deferred by design): special sessions poison daily-derived
gates.** A Muhurat/drill session's tiny range makes the NEXT session flag
NR7 (pinned by an adversarial characterization test); the same sessions
distort the CPR-width reference and gap baselines marginally. The designed
fix is the session-type feature (F4 in `MARKET_CONTEXT_FEATURES.md`), which
is not yet built. ~17 special sessions in 10 years — impact is rare and
bounded.

**F5 — Minor: `volume_ratio` includes the current bar in its rolling mean**
(documented archive semantics). A breakout bar's own surge slightly inflates
its baseline, making the gate marginally conservative. Superseded by the
roadmap's time-of-day RVOL (A1) when built; no change now.

**F6 — Minor: consecutive inside bars (ii patterns) re-anchor to the newer,
tighter mother bar**; part of the practitioner literature keeps the original
mother. Deterministic, defensible, documented here.

**F7 — Minor: `crossed_above` at session boundaries compares yesterday's
close against yesterday's LEVEL VALUE** (the reference series shifts too),
so a bar-0 fire on prior-day-level strategies is possible when a session
opens beyond the level (the "gap-over" case). Deterministic; consistent with
the documented gap-over failure modes; noted for completeness.

**F8 — Minor: `prepare()` raises on the store's EMPTY-frame representation**
(object-dtype columns from `store.read` of a missing symbol). Every platform
caller guards with `bars.empty` before `prepare` (verified:
`prepare_signals`, scanner), so no production path is affected; the strategy
API is simply not defensive against degenerate direct calls.

**F9 — Informational (latent, live-scanning only): `supertrend` is
path-dependent from series start.** Recomputing it on the scanner's rolling
tail windows can differ from full-history values near window edges.
Backtests (full frames) are unaffected. The live integration of
`supertrend_15m` must feed long/full history — recorded as a production
blocker input in `PRODUCTION_READINESS_REPORT.md`.

**F10 — Informational: `meta.expected_behaviour` strings for orb/cpr
overstate entry discipline** pending the F3 decision (descriptive text, no
behavioural effect).
