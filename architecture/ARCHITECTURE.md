# Algo Trader - Architecture

_Last updated: 2026-07-14. Reflects the implemented system, not aspiration._

## 1. Overview

A modular, adaptive trading framework on Freqtrade. The Freqtrade strategy
class is a thin orchestrator; all trading logic lives in importable,
independently testable modules under `user_data/strategies/algo_core/`. Every
tunable value has exactly one definition, in `settings.py`.

```
                        AdaptiveTrendStrategy (IStrategy)
                                    │ orchestrates only
     ┌──────────┬───────────┬───────┼────────┬────────────┬───────────┐
     ▼          ▼           ▼       ▼        ▼            ▼           ▼
 settings   indicators   regime   profiles  decision   risk_engine  trade_
 (single    (all TFs)   Detector  (styles)  _engine    (stops,      manager
  source)                                    (GO/NOGO)  cap, size)  (exits)
```

Data flow per 5m candle:

1. **Settings** - `AlgoSettings.from_config(config)` builds the frozen params
   (indicators, engine, risk) once. Everything downstream reads from it.
2. **Indicators** - `populate_indicators` computes the canonical indicator set
   on the 5m base frame and on 15m / 1h / 4h informative frames, merged
   lookahead-safe via `merge_informative_pair` (suffixes `_15m`, `_1h`, `_4h`).
3. **Candidate gate** - the active profile's vectorized `entry_signal` marks
   candidates using the MANDATORY, candle-based conditions only.
4. **Regime routing** - `confirm_trade_entry` asks the `RegimeDetector` for the
   current regime and skips the trade if the active profile does not support it.
5. **GO / NO-GO** - the `DecisionEngine` scores the candidate: all mandatory
   gates must pass AND the advisory conviction score must reach `min_score`.
   Any rejection is recorded with reasons and regime.
6. **Sizing** - `custom_stake_amount` uses the fixed config stake by default,
   or risk-based sizing when explicitly enabled.
7. **Management** - `custom_stoploss` ratchets the stop tighter (never wider);
   `populate_exit_trend` fires objective trend-failure exits. No ROI-table or
   time-based exits.

## 2. Single source of truth (`settings.py`)

Three frozen dataclasses hold every threshold; `AlgoSettings` bundles them and
is built from `config['algo_trader']`. Any field is overridable from config;
unknown keys are ignored.

| Params           | Holds                                                    |
|------------------|----------------------------------------------------------|
| `IndicatorParams`| EMA periods per timeframe, ADX/RSI/ATR periods, windows  |
| `EngineParams`   | trend/liquidity gates, RSI/volume/volatility, weights, `min_score` |
| `RiskParams`     | stop multiples, hard cap, tiers, exit floors, sizing toggle |

No module defines its own thresholds. The profile's vectorized gate and the
decision engine's per-trade checks read the *same* `EngineParams`, so tuning
one config value changes both consistently.

## 3. Timeframe roles

| TF  | Role                | EMA (fast/slow, from settings)      |
|-----|---------------------|-------------------------------------|
| 4h  | Market regime       | 21/50                               |
| 1h  | Intermediate trend  | 21/50 + ADX + closing higher        |
| 15m | Entry trend         | 20/50 + ADX + structure             |
| 5m  | Execution           | 9/21 + cross trigger                |

**Warmup:** `startup_candle_count` is derived (not hardcoded) from the periods:
the 4h EMA50 binds at `50 x (240/5) = 2400` base candles, x1.5 safety = **3600**.
Freqtrade fetches each informative timeframe at its own resolution, so 3600
base candles maps to a modest ~75 4h candles.

## 4. GO / NO-GO decision engine (`decision_engine.py`)

Two kinds of check. A trade is GO only if **all mandatory gates pass** AND the
**advisory conviction score** reaches `min_score` (default 0.60).

**Mandatory gates** (block on failure; do not contribute to the score):

| Category         | Checks                                             |
|------------------|----------------------------------------------------|
| trend            | htf_trend (4h), itf_trend (1h+ADX), entry_trend (15m+5m) |
| market_structure | rising 15m swing lows + 1h closing higher          |
| liquidity        | avg quote volume floor; order-book spread (when available) |
| risk             | risk/reward >= min; required stop <= 6% hard cap; stake > 0 |

**Advisory signals** (feed the weighted conviction score only):

| Check      | Weight | Verifies                                    |
|------------|--------|---------------------------------------------|
| momentum   | 0.40   | 15m RSI in band (graded around the ideal)   |
| volume     | 0.35   | 5m volume vs. its mean (graded)             |
| volatility | 0.25   | 15m ATR% inside the tradeable band          |

`score = Σ(weight · advisory_score) / Σ(weight)`. Weak advisory quality alone
can therefore reject an otherwise-valid setup (low conviction), while no single
advisory check is ever a hard gate.

Every NO-GO is appended to `user_data/logs/trade_rejections.jsonl` (timestamp,
pair, regime, score, per-check category/advisory/pass/reason) and logged.

Thresholds override via config:

```json
"algo_trader": {
    "active_profile": "trend_following",
    "engine": { "min_score": 0.65, "adx_min_1h": 25 },
    "risk":   { "enable_risk_sizing": false }
}
```

## 5. Regime detector (`regime.py`)

`RegimeDetector.detect(row)` classifies the market into a `MarketRegime`.
It currently always returns `TREND`; the `_classify` hook is the documented
extension point for RANGE / VOLATILE. The strategy consults it in
`confirm_trade_entry` and skips any trade whose regime is not in the active
profile's `supported_regimes`. Because routing lives in the strategy and
profiles declare their own supported regimes, adding regimes later needs **no
change to the Freqtrade interface**.

## 6. Risk engine (`risk_engine.py`)

* **Initial stop** = wider of (2.0 x 15m ATR) and (distance below the 15m swing
  low), as a fraction of price - beyond both noise and structure.
* **Hard emergency stop**: class-level `stoploss = -0.06`. If the required stop
  exceeds 6%, the decision engine rejects the trade. Note: a market stop with
  `stoploss_on_exchange: False` targets 6% under normal fills but does not
  guarantee it across gaps/flash-crashes (see limitations).
* **Progressive profit locking** (price ratios vs. open): +1.0%->+0.1%,
  +1.8%->+0.8%, +2.8%->+1.5%, +4.5%->+2.8%.
* **ATR trail**: once profit >= 0.6%, a chandelier trail at 2.0 x 15m ATR.
* **Position sizing** (`risk_based_stake`): stake = wallet x `risk_per_trade` /
  stop distance, clamped to `[min_stake, max_stake]` - *not* to the proposed
  stake. Disabled by default (`enable_risk_sizing = False`), in which case the
  fixed config stake is used. Enabling requires config
  `"stake_amount": "unlimited"`, or Freqtrade caps the stake upstream.

All functions are pure (no Freqtrade imports).

## 7. Trade manager (`trade_manager.py`)

* Persists the initial stop on the trade and returns the running **maximum** of
  {initial stop, unlocked tiers, ATR trail} from `custom_stoploss`. Stops are
  monotonic by construction; Freqtrade also refuses stop reductions.
* Objective exit conditions (tagged for audit), thresholds from `RiskParams`:
  * `exit_1h_trend_reversal` - 1h fast EMA crosses below slow EMA.
  * `exit_trend_exhaustion` - 15m ADX < `exit_adx_floor` while price loses 15m slow EMA.
  * `exit_momentum_breakdown` - 15m RSI < `exit_rsi_floor` while price loses 15m fast EMA.
* **No time exits.** The 30-120 min average holding target is an emergent
  property of the timeframe/indicator choices, verified empirically at the
  backtest stage - deliberately not a rule.

## 8. Strategy profiles (`profiles/`)

`StrategyProfile` (base.py) defines the interface: `entry_signal`,
`exit_signal`, a `required_columns` contract, a `supported_regimes` tuple, and
an `enabled` flag; all profiles share one constructor `(settings, trade_manager)`
so the registry builds any of them uniformly. The registry activates exactly
one profile; disabled profiles cannot be activated.

| Profile              | Status   |
|----------------------|----------|
| trend_following      | ACTIVE   |
| pullback_trend       | scaffold |
| breakout             | scaffold |
| mean_reversion       | scaffold |
| volatility_expansion | scaffold |

The active profile's `entry_signal` applies only the mandatory candle-based
gates (trend + structure + 5m execution), reading thresholds from settings.
Advisory conditions are evaluated per-trade by the decision engine.

## 9. Validation strategy

Freqtrade has no `test-strategy` command. The project equivalent is
`user_data/scripts/validate_strategy.py`, which - without downloading market
data - loads the strategy through Freqtrade's resolver and exercises the
pipeline, the mandatory/advisory decision logic, the risk engine and trade
manager, the profile registry, the single-source settings (including config
override and gate-follows-settings), and the regime detector. Run it plus
`list-strategies` after any strategy-layer change:

```
docker compose run --rm freqtrade list-strategies --config user_data/config.json
docker compose run --rm --entrypoint python3 freqtrade user_data/scripts/validate_strategy.py
```

## 10. Known limitations

* Long-only (Binance spot). Short logic is out of scope until margin is.
* The 6% hard stop is not guaranteed across gaps/flash-crashes with
  `stoploss_on_exchange: False`; add exchange stops + protections before live.
* The spread gate self-disables where no order book exists (backtests).
* All thresholds are initial values - untuned until the first backtest slice;
  treat them as calibration starting points, not truth.
* `wallets` is unavailable in some utility contexts; risk sizing then falls
  back to the proposed/config stake.
