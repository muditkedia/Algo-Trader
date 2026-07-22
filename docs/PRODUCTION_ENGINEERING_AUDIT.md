# Production Engineering Audit

_Audit and behavior-preserving engineering pass: 2026-07-23_

## Executive assessment

The repository is production-oriented and materially stronger than a prototype.
Market data has one runtime owner, strategy registration is discoverable and
tested, execution declarations are explicit, the paper/live seam is injectable,
recovery is first-class, and dashboard observation is isolated from trading.
The complete suite had 854 collected tests before this pass.

The highest-risk maintenance area is not a missing framework feature. It is the
size and responsibility of a few already-tested orchestration classes. This
pass deliberately leaves those trading-critical classes structurally intact so
that operational quality improves without changing entries, exits, sizing,
risk, scheduling, persistence, or broker behavior.

## Architecture

### Strengths

- `MarketState` is the single market-data read surface for scanning, risk,
  execution marks, and dashboard reporting.
- The local candle engine and streaming source separate tick normalization,
  candle construction, gap repair, and provider fallback.
- `ExecutionSpec` keeps strategy-owned exits and targets declarative while the
  live manager and backtest engine share the same execution contract.
- Recovery, portfolio persistence, risk gates, and broker adapters have clear
  seams and are exercised with adversarial and restart tests.
- Dashboard export is observational, atomically written, and guarded so an
  exporter failure cannot stop a trading cycle.
- Static analysis found no Python import cycles.

### Maintainability risks retained intentionally

- `ProductionEngine`, `DashboardExporter`, `MarketDataService`, and
  `TimeframeScheduler` are large cohesive coordinators. Splitting them would
  require a longer paper-trading equivalence period and is deferred.
- `execute_signal`, `TradeManager.manage`, and several research routines are
  long stateful functions. Their behavior is heavily covered; broad extraction
  is deferred rather than risk decision drift.
- Strategy modules contain small repeated interface adapters. They are
  required by the common strategy contract and are not duplicate trading
  implementations.
- There is no configured Ruff/MyPy/coverage gate. Introducing one would be a
  separate tooling decision; this pass does not add a new dependency or alter
  the supported runtime.
- The `architecture/` directory referenced by repository instructions is
  absent. Architecture guidance currently lives in `docs/` and remains there.

## Robustness

The normal startup, reconnect, shutdown, atomic persistence, and recovery paths
were reviewed. The event journal had one narrow crash-boundary weakness: an
interrupted final JSONL write could make the whole day's read fail. The reader
now ignores only that incomplete final record and still raises on interior
corruption.

Broad exception boundaries in provider, dashboard, and shutdown code are
intentional fault isolation. They are retained, with operational logging, so a
display or broker cleanup problem cannot become a trading decision change.

## Performance and memory

The checked-in dashboard snapshot set was approximately 268 KB, of which
approximately 267 KB was slow-tier data; `logs.json` was the dominant payload.
The exporter now uses compact JSON without changing the schema or fields. The
browser also prevents overlapping refresh requests and suspends polling while
hidden, while retaining the existing one-second/live and five-second/slow
cadences.

The exporter still reads the bounded daily event journal for its operational
panels. A larger incremental event-index design would be a backend architecture
change and is intentionally deferred.

## Logging, configuration, and dependencies

Logging is namespaced and contextual at provider, trading, dashboard, recovery,
and persistence boundaries. Startup and exception messages were reviewed; no
safe noise reduction was identified that would preserve the same operational
diagnostic value. Configuration is dataclass-based with explicit defaults and
validation at the trading seam. No setting was proven unused, and no dependency
was unquestionably removable, so neither configuration semantics nor the
dependency set was changed.

## Dashboard assessment

The original dashboard had correct data coverage but a sparse desktop grid and
an oversized sticky header on mobile. It also described short positions with
long-only wording and percentages. The frontend now uses an explicit dense
operations grid, a compact mobile header, responsive metric cards, direction
badges, direction-correct explanatory text, reduced-motion support, and native
visibility-aware polling. All existing panels, metrics, tables, notifications,
and explainability content remain available.

## Refactoring summary

| File | Change and benefit | Trading impact |
|---|---|---|
| `dashboard/index.html` | Added stable panel roles and layout hooks; improved status semantics. | None |
| `dashboard/styles.css` | Dense desktop grid, responsive mobile layout, direction badges, accessibility and reduced-motion rules. | None |
| `dashboard/app.js` | In-flight polling guards, hidden-tab pause/resume, shared DOM writer, long/short wording. | None |
| `src/algo/trading/dashboard.py` | Compact JSON; direction-correct display percentages, distance, headlines, and trailing explanations. | None; dashboard presentation only |
| `src/algo/trading/eventlog.py` | Tolerates only an incomplete final record after interrupted append. | None for valid logs and persistence semantics |
| `tests/test_dashboard_export.py` | Compact snapshot regression coverage. | None |
| `tests/test_eventlog_resilience.py` | Final-line and interior-corruption coverage. | None |
| `tests/test_dashboard_frontend.py` | IDs, panel, lightweight-static, and snapshot-name contracts. | None |
| `docs/DASHBOARD_GUIDE.md` | Correct file count/cadence and responsive behavior documentation. | None |
| `docs/PROJECT_STATE.md` | Records this pass and its boundaries. | None |

## Deferred work

Large-scale refactors of `ProductionEngine`, `TradeManager`, `execute_signal`,
the dashboard exporter backend architecture, strategies, scanner orchestration,
and recovery are intentionally deferred until a longer production paper-trading
equivalence period exists.

## Validation record

- Python compilation: passed for `src`, `tests`, and `scripts`.
- Focused dashboard/event-log/consistency suite: 25 passed.
- Paper, deployment, integration, and release-audit checks: 34 passed.
- Complete regression suite: 861 passed in 183.67 seconds.
- Browser verification: desktop 1280×720 and mobile 390×844; all 13 panels
  rendered, no horizontal overflow, no console errors, and the mobile header
  reduced from approximately 403px to approximately 137px.
- Snapshot comparison: 268.2 KB pretty-printed baseline versus an estimated
  229.4 KB compact representation, a 14.5% reduction.
- Runtime verification: `run_paper.py --help` and `run_trading.py --help`
  completed successfully; no credentials or live orders were used.
