# SCAN UNIVERSE & STRATEGY ACTIVATION

_Written 2026-07-20; strategy inventory updated 2026-07-22. Answers three
operator questions: how many symbols the engine scans and why, how the universe
is configured, and which intraday strategies participate in scanning._

## 1. Why the engine scanned only 99 symbols

**Configuration + data, not a bug.** The default config uses an explicit
`symbols_file` (`nifty100.txt`, 100 names) and the feed keeps only symbols
that actually have bars in the trading timeframe. 99 of those 100 have 15m
data, so the watchlist was 99. Nothing was silently dropped beyond that.

For production you now declare a **universe** instead of a fixed file.

## 2. The configurable market universe

`algo/trading/universe.py`. Declare a tier in the config:

```json
{ "timeframes": ["5m"],
  "universe": { "tier": "paper", "min_avg_traded_value": 10000000 } }
```

| Tier | Default target |
|---|---|
| `dev` | 100 |
| `paper` | 500 |
| `production` | 1000 |

Nothing is hardcoded to 1000: `size` overrides the tier, and every floor is
configurable (`min_price`, `max_price`, `min_avg_traded_value`,
`min_history_bars`, `max_stale_days`, `sources`, `exclude`, `lookback`).

**Selection.** Candidates are the union of the configured source lists
(`nifty500.txt`, `microcap250.txt`, `nifty100.txt` by default - 745 distinct
names). Each must be **tradeable** - enough history in the trading timeframe
and a recent bar (a stale series means suspended/delisted) - then pass the
price and liquidity floors. Survivors are **ranked by average daily traded
value** and the best N are kept.

**On market capitalisation:** the candle store holds no market-cap field, so
ADTV is the ranking key. It is the metric that decides whether an order can
actually be filled, and for Indian equities it tracks market cap closely. The
source lists are themselves market-cap tiers (NIFTY 500 + Microcap 250), so
cap is reflected through them. This is stated rather than implied - the engine
does not claim a market-cap input it does not have.

**Liquidity is measured from daily bars where they exist, and otherwise
derived from the trading timeframe** (traded value summed per session,
averaged over the lookback). Without that fallback, names with intraday but no
daily history were dropped silently; fixing it took the tradeable 5m universe
from 98 to 335 symbols.

### What is achievable today (measured)

| Tier | Timeframe | Target | Selected | Limiting factor |
|---|---|---|---|---|
| dev | 15m | 100 | **99** | only 99 names have 15m data |
| paper | 5m | 500 | **335** | only 340 names have 5m data |
| production | 5m | 1000 | **335** | same |

The universe machinery supports 1000; the **data does not yet**. Reaching a
1000-symbol production universe needs 5m history for roughly 665 more symbols
- an acquisition step, not a code change. The startup report always states how
far short of target it is and why:

```
universe[production] 335/1000 symbols from 745 candidates
  dropped: below_quality_floor=5, no_intraday_data=405
  note: 665 short of the 1000 target: only 335 candidates qualify with 5m
        data today - widen the sources or download more history
```

## 3. Strategy activation: 17 registered, 17 scanning

The production library now contains only the 17 enabled intraday strategies
(10 x 5m, 6 x 15m, 1 x 1h). All 17 declare `HoldingScope.INTRADAY`, are discovered
automatically, and participate in live scanning. No daily, swing, positional,
or overnight strategy remains registered.

The engine still enforces its intraday invariant independently of registration:
new entries stop at the cutoff and every open position is squared off before
the close. The generic research framework and historical research records are
retained for reproducibility; they are not registered trading strategies.

Verified programmatically by
`tests/test_strategy_participation.py` and
`tests/test_universe_and_reporting.py`. Regenerate the live inventory with
`scripts/strategy_report.py`.
