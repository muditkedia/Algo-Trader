# BACKTEST ENGINE AUDIT

_Independent verification phase, 2026-07-19. Subject: the strategy-owned
execution engine (`src/algo/execution/`), the standardized runner
(`scripts/backtest_50k.py`), and the statistics battery
(`algo/research/validation/metrics.py`). Method: line-by-line adversarial
review, hand-computed accounting checks, pathological-input testing
(`tests/test_adversarial_verification.py`), and cross-verification against
the frozen research simulator on clean paths. The frozen research pipeline
itself was NOT modified and retains its own characterization tests._

## Verified semantics (each item exercised by at least one test)

| Area | Verdict | Evidence |
|---|---|---|
| **Order sequencing (intrabar)** | ✔ pinned | stop → target → square-off → trail → horizon; entry bar can never exit (management starts at the next bar) — engine tests |
| **Entry fills** | ✔ | signal-bar close, only when a same-session management bar exists; last-bar signals untradeable (0 overnight trades across the corrected full run) |
| **Stop fills** | ✔ honest | at the level intrabar; at the OPEN through gaps (`gap_fill` flagged); flash-crash test: −20% open fills at −20%, not at the stop |
| **Target fills** | ✔ honest | at the level; at the better open through gaps |
| **Same-bar ambiguity** | ✔ pinned pessimistic | a bar spanning both stop and target is a STOP-OUT (test incl. the insane gap-above-target + trade-below-stop bar) |
| **Partial exits** | ✔ | one partial at T1, stop→breakeven, remainder to T2/stop/square-off; last-manageable-bar partial books then squares off same bar; per-leg costs hand-verified against the real NSE model (per-order brokerage caps apply per leg) |
| **Trailing updates** | ✔ monotonic | chandelier parity with the frozen simulator on clean paths (bit-equal exits); `column` trail ratchets only upward and exits AT the trailed line; NaN trail values ignored |
| **Position accounting** | ✔ by design | one position per signal, fractional qty = stake/entry; every signal independent — **no cross-signal netting in the backtest runner (documented convention, see below)** |
| **Capital accounting** | ✔ | fixed ₹50k notional per trade; net = gross − costs verified incl. an extreme-slippage (100 bps/side) model |
| **P&L calculations** | ✔ | profit_abs = profit_ratio × stake; partial legs summed on gross; hand-checked |
| **Drawdown calculations** | ✔ | `max_drawdown_abs` hand-checked on a crafted trade set (peak-to-trough of the close-date-ordered equity curve) |
| **Statistics** | ✔ | PF, win rate, expectancy, net hand-checked; expectancy rounded to 6dp for reporting (cosmetic) |
| **Multi-day handling (swing)** | ✔ | horizon parity with the frozen simulator; overnight gap-through stops fill at the open; consecutive gap days handled |
| **Session boundaries** | ✔ structural | intraday loop cannot cross a session; square-offs verified to land on the session's actual last bar (0 violations in the full corrected run) |
| **Market holidays** | ✔ | prior-day levels reference the prior TRADED session (holiday-skip test) |
| **Muhurat / special sessions** | ✔ engine-side | a 2-bar evening session enters and squares off correctly. (Strategy-side gate distortion is F4 in the strategy audit) |
| **Look-ahead bias** | ✔ | every prepared column re-derived for causality; platform truncation-test convention in place; engine reads only bars ≤ current index |
| **Timestamps / timezones** | ✔ with one latent hazard | store is tz-aware UTC; NSE sessions never cross UTC midnight so UTC-normalized session keys are safe for intraday. **Latent hazard (Major, future work):** 1d bars are stamped 18:30 UTC = IST midnight of the session date, so UTC-normalizing DAILY bars yields the prior calendar day. No current code joins 1d to intraday frames by date; the future feature layer (daily ATR/CPR onto intraday) MUST account for this one-day label shift or it will introduce look-ahead/misalignment |
| **Portfolio accounting** | N/A in backtests (by design) | see below |

## Documented conventions (not bugs — pinned by tests)

1. **Pessimistic same-bar resolution**: stop before target; OHLC cannot
   order intrabar touches.
2. **Partial-then-breakeven sequencing**: the breakeven stop applies from
   the NEXT bar (the partial bar's own low was already adjudicated against
   the pre-partial stop at that bar's start).
3. **Entry-bar immunity**: the entry bar cannot stop out the trade
   (inherited platform convention, pinned since Phase 16).
4. **Garbage-in contract**: the engine assumes store-quality bars. NaN bars
   mid-trade neither crash nor poison P&L (tested), but a NaN close on the
   square-off bar would propagate — impossible for store data (the quality
   gate rejects NaN rows).
5. **No portfolio netting in the backtest runner**: every historical signal
   is simulated independently at fixed stake; aggregates are sums over
   signals. Stated in every report since the ₹50k baseline. Portfolio-level
   accounting exists only in the (never-live) paper engine and is untested
   against the strategy-owned execution model — carried as a production
   blocker, not a backtest bug.

## Runner findings

- **R1 (Needs Fix before the next run): `scripts/backtest_50k.py` carries a
  hand-maintained 7-strategy list** and does not include the five batch-2
  strategies. Which strategies the next standardized run covers is an owner
  decision, so the list was left untouched; it must be updated (or made
  discovery-driven from `holding_scope == INTRADAY`) before that run.
- The `--verify` mode still reproduces the recorded Phase-15 baseline
  exactly (checked this phase), and `--legacy` preserves the pre-fix path.
