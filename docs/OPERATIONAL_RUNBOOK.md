# OPERATIONAL RUNBOOK

_Written for an operator who has never seen the code. Everything needed to run,
supervise, stop, and recover the system is here — no tribal knowledge required.
Companion docs: `PAPER_TRADING_GUIDE.md`, `LIVE_TRADING_GUIDE.md`,
`DEPLOYMENT_CHECKLIST.md`, `FAILURE_MODE_ANALYSIS.md`._

## 0. What this system does, in one paragraph

It trades **long-only NSE intraday equity** strategies. Every trading day it
scans a watchlist on 15-minute (and 1-hour) bars, evaluates 12 frozen
strategies, and for each signal that passes account risk checks it buys a fixed
₹-stake position, then manages that position to its own stop / target / trailing
rule and **squares off before the close — nothing is ever held overnight**. It
runs in **paper** mode (simulated fills) or **live** mode (real orders via Angel
One). Both modes run the identical pipeline; only the execution adapter differs.

## 1. Layout you need to know

| Path | What it is |
|---|---|
| `scripts/run_trading.py` | the program you run |
| `.env` | broker credentials (never commit; never printed) |
| `user_data/trading/portfolio.json` | current positions/orders/P&L (auto-managed) |
| `user_data/trading/events-YYYYMMDD.jsonl` | the audit trail — one JSON line per event |
| `user_data/trading/summary-YYYY-MM-DD.json` | end-of-day summary |
| `user_data/trading/holidays.txt` | market holidays, one ISO date per line — **you maintain this** |
| `user_data/trading/KILL` | create this file to emergency-stop |
| `user_data/data/nse/` | the candle store |

## 2. Daily start (paper)

```
cd "C:\Projects\Algo Trading\Algo Trader"
.venv/Scripts/python scripts/run_trading.py --mode paper --once     # smoke check
.venv/Scripts/python scripts/run_trading.py --mode paper --loop     # run the day
```

**Before you walk away, confirm these four things in the startup output:**

1. `mode=paper  live_armed=False` (or the intended live line).
2. `PREFLIGHT PASSED` — if it says FAILED, trading did not start; see §6.
3. The `preflight risk_config` line shows the **stake, max capital and max
   positions you intended**. (Config keys that are misspelled are silently
   ignored and defaults apply — this line is your check.)
4. The recovery line shows the expected number of resumed positions and
   **no unexpected `orphan_broker` entries**.

## 3. What you should see while it runs

Each tick prints a dashboard:

```
 PRODUCTION TRADING  [PAPER]  status=ok
 cycles=12 errors=0 risk_tripped=False
 realized=+0  unrealized=-320  deployed=100000
 open=2 closed=1 wins=1 losses=0
 RELIANCE     orb_15m    qty=20 entry=2450.10 stop=2431.00 uPnL=-180
```

- `status=ok` — normal. `degraded` — data or broker hiccup (it keeps managing
  positions; investigate if it persists). `halted` — risk tripped; no new
  entries will be taken (see §5).
- `risk_tripped=True` means the daily loss limit, circuit breaker, or kill
  switch fired. This **latches** — it will not un-trip by itself.

## 4. Normal shutdown

Press **Ctrl-C**. The system persists state, writes the daily summary, and
disconnects. Stopping is always safe: state is written atomically after every
change, so there is no "bad moment" to stop.

Positions are **not** auto-closed by Ctrl-C. If you stop with positions open,
either restart (it resumes managing them) or square off manually at the broker.

## 5. Emergency stop

```
echo stop > user_data\trading\KILL
```

On its next tick the system squares off **every** open position and stops
taking new entries. Delete the file to allow trading again (you must also
restart if the risk engine latched).

## 6. Preflight failed — what each check means

| Check | Meaning if it fails | What to do |
|---|---|---|
| `broker_auth` | login/connect failed | check `.env`; run `scripts/smartapi_login_check.py` |
| `live_armed` | live mode but not fully armed | either arm all three keys or switch to paper |
| `market_data` | no symbol has candles | run the data download; check the store path |
| `watchlist` | symbols file empty/missing | fix `symbols_file` |
| `strategies` | no strategies loaded | code/config problem — escalate |
| `risk_config` | incoherent limits | fix the `risk` block (stake ≤ capital, positives) |
| `storage` | state dir not writable | fix permissions/disk space |
| `clock` | timezone problem | fix the host clock/timezone |

Trading does not start until every critical check passes. That is by design.

## 7. Restart / crash recovery

Just start it again with the same config. On startup it:

1. reloads its saved positions and orders,
2. asks the **broker** what actually exists (broker truth wins),
3. resumes managing positions that match (keeping their trailed stops),
4. closes in its book any position the broker no longer has,
5. **adopts and flags for immediate square-off** any broker position it does
   not recognise,
6. corrects quantities for partial fills,
7. never re-sends an order (no duplicate risk).

Everything it does is written to the event log as `recovery` events. Read them
after any unexpected restart.

**Do not run two instances at once** — there is no lock preventing it, and both
would manage the same positions.

## 8. Reading the audit trail

```
type user_data\trading\events-20260720.jsonl
```

Event types: `signal` (a strategy fired), `risk_block` (why an entry was
refused), `order`, `fill`, `position` (open/trail/partial/close), `recovery`,
`error`, `health`, `halt`. Every line has a UTC timestamp. This is the record
to consult for "why did/didn't it trade X".

## 9. End of day

The loop exits after the close once no positions remain, writing
`summary-YYYY-MM-DD.json` (trades, wins/losses, realized P&L, per-strategy
breakdown). Check: realized P&L matches your broker statement (live), and no
positions were left open.

## 10. Weekly / periodic maintenance

- Update `holidays.txt` before each month's holidays.
- Refresh the candle store (see the data download script) so strategies have
  current history.
- Review `summary-*.json` and event logs for repeated `risk_block` or `error`
  patterns.
- Before a symbol's **corporate action** (split/bonus), remove it from the
  watchlist for that day — candles are unadjusted and a price jump can produce
  a false signal.

## 11. Going live (summary — full procedure in LIVE_TRADING_GUIDE.md)

Live requires **three** keys simultaneously; missing any one makes real orders
impossible:

1. `mode: "live"` in the config,
2. `live_trading_enabled: true` in the config,
3. `ALGO_ENABLE_LIVE=YES` in the shell environment.

Never put key 3 in a permanent profile. Before the first live session, complete
the supervised single-order smoke test in `FINAL_OPEN_ITEMS.md` — the live
order API has not yet been exercised.

## 12. Escalation triggers (stop trading and investigate)

- `status=halted` unexpectedly, or `risk_tripped=True` early in the day.
- Repeated `error` events for the same component.
- A `recovery` event reporting `orphaned_broker` positions you cannot explain.
- Broker positions that disagree with the dashboard.
- Any raw traceback in the console (should not happen; capture the log).
