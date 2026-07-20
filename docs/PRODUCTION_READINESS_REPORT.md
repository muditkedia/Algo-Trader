# PRODUCTION READINESS REPORT

_Independent verification phase, 2026-07-19. Statuses: ✅ Production Ready ·
⚠ Needs Fix · ❌ Blocking Issue. "Production" here means the intended
automated intraday trading system; no live/paper/broker execution work was
performed or reviewed as running software (none exists in operation)._

## Subsystem review

| Subsystem | Status | Assessment |
|---|---|---|
| **Data layer** (store, ingestion, quality, audit, manifest) | ✅ (with one open decision) | Battle-tested this project: idempotent upserts, head-gap backfill, loud quarantines, manifest provenance, formal audit tool. Open item: **NIFTY50 15m context series absent** (quarantined; remediation options approved-pending decision) — blocks the index-alignment feature, not backtesting. Unadjusted candles are a documented data property |
| **Strategy implementations** (12 intraday) | ✅ after fixes | Full audit (`STRATEGY_AUDIT_REPORT.md`): all causal, all spec-owning, two objective bugs fixed (F1/F2), one owner decision open (F3 re-entry on orb/cpr), minor findings documented. Daily swing strategies (23) were not re-audited this phase (research-frozen, out of intraday scope) |
| **Shared feature layer** | ⚠ (not built — planned) | `algo/features/` exists only as an approved design (`MARKET_CONTEXT_FEATURES.md`). Required before practitioner-rule upgrades; **must respect the 1d↔intraday timestamp-normalization hazard documented in the engine audit** |
| **Execution engine** (`algo/execution`) | ✅ | Adversarially audited; honest fills, structural session safety, partials, both trail modes; 100+ tests across the engine suites |
| **Backtesting engine / runner** | ⚠ Needs Fix | Engine ✅; the standardized runner's hand-maintained strategy list omits the five batch-2 strategies — update (or make discovery-driven) before the next run (owner choice of scope) |
| **Portfolio accounting** | ❌ Blocking for live | The backtest runner is per-signal by design (documented, fine for measurement). The REAL portfolio layer (paper engine's manager: capacity, exposure, budgets, netting) predates the strategy-owned execution model and has never run against it — unverified end-to-end |
| **Risk management** | ⚠ Needs Fix for live | Pure functions tested. Two live-gaps: (1) structural stops (OR-low, CPR-bottom, prev-day-low…) carry **no account-level hard cap by design** — a live layer needs a max-loss-per-trade guard independent of strategy declarations; (2) portfolio-level budgets untested with owned execution |
| **Scanner** | ❌ Blocking for live | Built for the pre-reset uniform-exit world; not integrated with ExecutionSpec (would manage positions with the old model); `supertrend_15m` additionally needs full-history windows (path-dependent indicator — audit F9) |
| **Configuration** | ✅ | Env-only credentials, masked secrets, dataclass params frozen per module |
| **Logging** | ✅ | Structured, loud quality/quarantine paths, manifest provenance |
| **Error handling** | ✅ engine/data · ⚠ API edge | Per-symbol failures never abort batches; engine survives pathological bars. Strategy `prepare()` is not defensive against the store's empty-frame representation (all platform callers guard — F8) |
| **Test coverage** | ✅ | **489 passed, 1 skipped**: engine semantics, adversarial battery, per-strategy signal tests, data-layer, research-freeze characterization. Weakest area: paper/scanner path vs the new execution model (matching their ❌ above) |
| **Documentation** | ✅ | Library reference, implementation guide, data reports, audits — current as of this phase |

## Blockers for safe live deployment (complete list)

1. **No live order path exists** (deliberate to date) — everything below
   precedes building one.
2. **Scanner + paper/portfolio layer are pre-reset**: they impose the old
   uniform exits and have never executed a strategy-owned spec. Integration
   + end-to-end tests required (❌).
3. **Account-level risk guard** for uncapped structural stops, plus
   portfolio budget verification under the new model (⚠/❌).
4. **Supertrend live-window semantics** (full-history feed or precomputed
   state) before `supertrend_15m` can be scanned live (F9).
5. **Runner strategy list** update before the next standardized backtest
   (⚠, trivial).
6. **NIFTY50 15m decision** — required for the index-alignment feature tier,
   not for backtesting (open remediation options stand).
7. **Owner decision on F3** (orb/cpr once-per-session) so documentation and
   behaviour agree before production baselines are declared.

## Bug fixes applied this phase (Part 4 summary)

| # | Bug | Root cause | Files changed | Tests added | Before → After |
|---|---|---|---|---|---|
| 1 | `nr7_intraday_15m` re-entered on every re-cross of the narrow-day high (36.4% of signal sessions had multiple same-day entries) | `crossed_above` re-fires on each excursion; the canonical Crabel day-trade is ONE breakout per expansion day and the cap was missing | `src/algo/strategies/library/nr7_intraday_15m.py` | `test_nr7_intraday_takes_only_the_first_break_of_the_session` | 2,553 → 1,626 signals on the 10-symbol sample; multi-entry sessions 592 → 0 |
| 2 | `gapgo_15m` re-entered on re-crosses of the first-bar high (11.2% of signal sessions), contradicting its own documented one-shot behaviour | same missing once-per-session cap | `src/algo/strategies/library/gapgo_15m.py` | `test_gapgo_takes_only_the_first_break_of_the_morning` | 89 → 80 signals; multi-entry sessions 9 → 0 |

No other code was changed: every remaining finding is either a pinned
convention, a documented limitation awaiting its designed fix, or an owner
decision (F3). No recorded result, frozen research artifact, or existing
strategy rule outside the two fixes was touched. Nothing committed.
