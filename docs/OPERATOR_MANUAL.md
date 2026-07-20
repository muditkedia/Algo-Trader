# OPERATOR MANUAL — start here

_Complete step-by-step guide to running the trading platform. **No programming
knowledge assumed.** Every command is written exactly as you should type it.
Windows paths are used throughout because that is this machine's setup._

> **The one-line version.** Open VS Code → open a terminal → run
> `.venv\Scripts\python scripts\run_trading.py --mode paper --loop --serve-dashboard`
> → open `http://localhost:8787` in your browser → watch → press **Ctrl-C** at
> the end of the day.

---

# PART 1 — ONE-TIME SETUP (do this once)

## 1. Open the project in VS Code

1. Open **VS Code**.
2. **File → Open Folder…**
3. Choose exactly this folder:
   `C:\Projects\Algo Trading\Algo Trader`
4. You should see folders named `src`, `scripts`, `docs`, `dashboard`,
   `user_data` in the left sidebar. If you don't, you opened the wrong folder.

**Always open this folder — not a sub-folder.** Every command below assumes you
are in it.

## 2. Open the terminal (which terminal to use)

- In VS Code: **Terminal → New Terminal** (or press `` Ctrl+` ``).
- A panel opens at the bottom. Use **PowerShell** (the default on this machine).
- The prompt should show:
  `PS C:\Projects\Algo Trading\Algo Trader>`

If it shows a different folder, type this and press Enter:

```
cd "C:\Projects\Algo Trading\Algo Trader"
```

## 3. The virtual environment

A virtual environment (`.venv`) is a private copy of Python for this project.
It already exists.

**You do not need to "activate" anything.** Every command in this manual calls
the environment's Python directly:

```
.venv\Scripts\python
```

*(If you prefer activating it, run `.venv\Scripts\Activate.ps1` and then you
can type just `python`. Optional — the manual assumes you do NOT.)*

## 4. Install dependencies (only if something is missing)

Normally already done. To verify:

```
.venv\Scripts\python -c "import pandas, numpy; print('core OK')"
```

Expected output: `core OK`

If it errors, install everything:

```
.venv\Scripts\python -m pip install -e .
```

**Optional extras:**

```
.venv\Scripts\python -m pip install smartapi-python pyotp   # needed for LIVE only
.venv\Scripts\python -m pip install psutil                  # dashboard CPU/RAM gauges
```

## 5. SmartAPI credentials (broker login)

Needed for **live trading** and for **real-time data**. Paper mode works
without them (it uses stored historical candles).

1. Log in to the Angel One SmartAPI portal and create an app to get your
   **API key**.
2. In VS Code, open (or create) the file named `.env` in the project root.
3. Put these five lines in it, replacing the values with your own:

```
SMARTAPI_API_KEY=your_api_key_here
SMARTAPI_CLIENT_CODE=your_client_code_here
SMARTAPI_PIN=your_login_pin_here
SMARTAPI_TOTP_SECRET=your_base32_totp_secret_here
```

4. **Never share this file or commit it to git.** The system never prints these
   values in any log.
5. Test the login:

```
.venv\Scripts\python scripts\smartapi_login_check.py
```

Expected ending:

```
      OK - account: YOUR NAME, exchanges: ['nse_cm', 'bse_cm']
[5/5] token refresh + logout...
      OK

SMARTAPI LOGIN CHECK PASSED
```

## 6. Configuration — what you may need to change

Defaults are safe and work as-is. To change anything, create a file named
`paper.json` in the project root:

```json
{
  "mode": "paper",
  "symbols_file": "nifty100.txt",
  "timeframes": ["15m"],
  "capital": {
    "deploy_today": 300000,
    "max_daily_loss": 10000
  }
}
```

### You set TWO numbers. The engine works out the rest.

| Value | What it means | Example |
|---|---|---|
| `deploy_today` | ₹ you are willing to put to work in **today's** session | `300000` |
| `max_daily_loss` | ₹ loss at which the system stops opening new trades today | `10000` |

Everything else is derived automatically — there is nothing else to set or
keep in sync:

| Derived automatically | Rule | With ₹3,00,000 / ₹10,000 |
|---|---|---|
| Portfolio value | = `deploy_today` | ₹3,00,000 |
| Max capital in any one trade | = `deploy_today` ÷ 2 | ₹1,50,000 |
| Risk allowed on a new trade | = whatever is **left** of `max_daily_loss` | ₹10,000 at open |

**Risk is managed across the whole book, not trade by trade.** The engine
continuously tracks:

```
Remaining risk budget = max_daily_loss
                      − realized loss so far
                      − open risk of existing positions   (entry → current stop)
                      − risk reserved by orders still working
```

Every new trade must fit inside what remains, so three trades can never each
risk the full limit. **As a trailing stop tightens, that position's open risk
falls and the freed budget becomes available again**; at breakeven it
consumes zero.

**How a trade gets sized.** The strategy supplies the entry and its own
stop-loss. The engine buys as much as the capital cap allows, then *reduces
the quantity* until the money at risk fits the remaining budget — and never
commits more capital than is still unused today. A wider stop therefore means
a smaller position, automatically. Quantities are always whole shares (and
whole lots where applicable). A trade is refused only when even one lot will
not fit.

*Worked example (`deploy_today` ₹3,00,000, `max_daily_loss` ₹10,000):* with
₹6,000 of open risk already on the book and ₹1,000 reserved by a working
order, ₹3,000 of budget remains. A signal at ₹100 with its stop at ₹97
(₹3/share) would naturally take 1,500 shares (₹4,500 at risk) — so it is cut
to **1,000 shares**, exactly ₹3,000 at risk.

To change other operational limits (rarely needed), add a `risk` block:
`max_open_positions` (5), `circuit_breaker_errors` (5), `lot_size` (1).

### Which stocks are scanned (the universe)

By default the engine scans the list in `symbols_file` (`nifty100.txt`). For a
bigger, automatically-chosen universe, add a `universe` block instead:

```json
{ "timeframes": ["5m"],
  "universe": { "tier": "paper", "min_avg_traded_value": 10000000 } }
```

| Tier | Target size |
|---|---|
| `dev` | 100 |
| `paper` | 500 |
| `production` | 1000 |

The engine picks the most liquid tradeable stocks up to that size, skipping
anything illiquid, too cheap, newly listed, or suspended. Startup prints how
many it selected and — if it is short of the target — exactly why. **Today the
data supports about 335 symbols on 5-minute bars**; more requires downloading
more history. Full detail: `docs/UNIVERSE_AND_STRATEGIES.md`.

> ⚠ **Spelling matters.** A misspelled setting is silently ignored and the
> default is used. Always confirm the values in the startup output
> (see §9, the `preflight capital_config` line).

## 7. Market holidays

Create/edit `user_data\trading\holidays.txt` — one date per line:

```
2026-08-15
2026-10-02
```

The system will not trade on these days. **Update this list each year.**

---

# PART 2 — DAILY OPERATION (PAPER MODE)

## 8. The exact command to start

In the VS Code terminal, from `C:\Projects\Algo Trading\Algo Trader`:

```
.venv\Scripts\python scripts\run_trading.py --mode paper --loop --serve-dashboard
```

That single command starts **both** the trading engine and the dashboard.

**Variations:**

| Purpose | Command |
|---|---|
| Run one cycle and stop (a quick test) | `.venv\Scripts\python scripts\run_trading.py --mode paper --once` |
| Run the full day | `.venv\Scripts\python scripts\run_trading.py --mode paper --loop --serve-dashboard` |
| Use your config file | add `--config paper.json` |
| Dashboard on a different port | add `--dashboard-port 8788` |
| Just print a text snapshot | `... --mode paper --dashboard` |

## 8b. Watching from your phone (same Wi-Fi)

The dashboard is automatically available to **every device on your Wi-Fi** —
no cloud, no setup, no file copying.

At startup the console prints something like:

```
  Dashboard available at:
    http://localhost:8787             (this laptop)
    http://192.168.1.14:8787          (most likely - this machine's active network connection)  <-- open this on your phone
    http://192.168.240.1:8787         (probably a virtual adapter (WSL/Hyper-V/Docker) - phones cannot reach this)
```

**On your phone:** connect to the same Wi-Fi, open Chrome/Safari, and type the
address marked **"open this on your phone"** — e.g. `http://192.168.1.14:8787`
(include the `http://` and the port). Bookmark it; the address only changes if
your router hands the laptop a new IP.

**QR code (optional).** If the `qrcode` package is installed, a scannable QR of
that address is printed at startup — just point your phone camera at it. To
enable: `.venv\Scripts\python -m pip install qrcode`. To hide it: add `--no-qr`.

**Windows Firewall.** The first time you run this, Windows may ask to allow
Python on the network. Tick **Private networks** and click **Allow access**.
If you dismissed it and the phone cannot connect, allow it manually:
*Windows Security → Firewall & network protection → Allow an app through
firewall → Allow another app… → browse to*
`C:\Projects\Algo Trading\Algo Trader\.venv\Scripts\python.exe` *→ tick Private*.

**Keep it to your own network.** To disable phone access and bind to the laptop
only, start with `--dashboard-host 127.0.0.1`.

### If the phone cannot connect

| Check | What to do |
|---|---|
| Same Wi-Fi? | Phone and laptop must be on the **same** network (not guest Wi-Fi, not mobile data — turn mobile data off to be sure) |
| Right address? | Use the one marked *"open this on your phone"*, not a virtual-adapter address, not `localhost` |
| Typed fully? | `http://192.168.1.14:8787` — with `http://` and `:8787` |
| Firewall | Allow `python.exe` on **Private** networks (above) |
| Works on the laptop? | Open the same LAN address in the laptop's browser. If that fails, the server isn't running — check the terminal |
| Router isolation | Some routers/guest networks block device-to-device traffic ("AP isolation"/"client isolation"). Use the main network or turn that setting off |
| IP changed | If you reconnected the Wi-Fi, the laptop may have a new IP — restart the engine and read the new address |

### Security — please read

The dashboard is **read-only**: it serves only GET/HEAD requests (POST/PUT/
DELETE are refused with 405), serves only the `dashboard` folder, and has **no
endpoint that can place, modify or cancel an order, change configuration, or
stop trading**. Nothing done in a browser can affect trading.

However, **anyone on your Wi-Fi can view it** — there is no password. It shows
your positions, P&L and risk limits. On your home network that is normally
fine. On shared/public Wi-Fi (café, hotel, office), start with
`--dashboard-host 127.0.0.1` so only your laptop can see it. Never
port-forward this to the internet.

## 9. What successful startup looks like

```
mode=paper  live_armed=False

  Dashboard available at:
    http://localhost:8787             (this laptop)
    http://192.168.1.14:8787          (most likely ...)  <-- open this on your phone
... preflight broker_auth    OK paper connected
... preflight market_data    OK 99/99 symbols have 15m data
... preflight watchlist      OK 99 symbols
... preflight strategies     OK 12 enabled intraday strategies
... preflight capital_config OK deploy_today=300,000 max_daily_loss=10,000 -> max/trade=150,000 risk/trade=12,000 max_pos=5
... preflight storage        OK user_data/trading
... preflight portfolio      OK 0 open positions loaded
... preflight clock          OK now=2026-07-20T09:14:03+05:30 session_open=True
... PREFLIGHT PASSED
================================================================
 CAPITAL & RISK BUDGET
================================================================
 Deploy Today                     Rs      300,000
 Max Daily Loss                   Rs       10,000
 Max Capital Per Trade            Rs      150,000
 Current Open Risk                Rs            0
 Reserved Pending Risk            Rs            0
 Remaining Portfolio Risk         Rs       10,000
================================================================
================================================================
 PRODUCTION TRADING  [PAPER]  status=ok
================================================================
```

**Four things to check every morning:**

1. `mode=paper  live_armed=False` — you are NOT risking real money.
2. `PREFLIGHT PASSED` — if it says FAILED, **nothing is trading**; see §10.
3. The `capital_config` line shows **your intended** deploy_today and
   max_daily_loss (and the caps derived from them).
4. `status=ok` in the dashboard box.

Then leave it running. New lines appear as the market moves.

## 10. Common startup errors and fixes

| What you see | What it means | Fix |
|---|---|---|
| `PREFLIGHT FAILED` + `broker_auth FAIL` | broker login failed | check `.env`; run `scripts\smartapi_login_check.py` |
| `preflight market_data FAIL 0/99 symbols` | no candle data | run the data download (§20) |
| `preflight storage FAIL` | cannot write files | check disk space / folder permissions |
| `preflight capital_config FAIL` | limits don't make sense | `deploy_today` and `max_daily_loss` must be > 0, and the loss limit smaller than the capital |
| `ModuleNotFoundError: No module named 'algo'` | wrong folder or wrong Python | `cd` to the project root; use `.venv\Scripts\python` |
| `missing SmartAPI credentials: ...` | `.env` incomplete | add the missing lines (§5) |
| `dashboard server could not start on 0.0.0.0:8787` | port already in use | add `--dashboard-port 8788` |
| Phone shows "site can't be reached" | firewall / wrong address / different Wi-Fi | see §8b troubleshooting table |
| `live mode but NOT armed - refusing to start` | live config without the env key | intended safety; see §14 |

## 11. How to stop paper trading safely

Click the terminal, then press **Ctrl-C** once.

```
interrupted - persisting state and exiting
daily summary written: user_data\trading\summary-2026-07-20.json
```

State is saved continuously, so stopping is **always safe**. Open positions are
NOT auto-closed — restart to keep managing them, or square off at your broker.

**Emergency stop** (closes everything immediately) — in a second terminal:

```
echo stop > user_data\trading\KILL
```

On the next cycle every position is squared off and no new trades are taken.
Delete the file (`del user_data\trading\KILL`) and restart to resume.

## 12. How to restart after stopping

Run exactly the same start command (§8). On startup the system automatically:

- reloads its saved positions,
- checks with the broker what really exists,
- resumes managing matching positions with their trailed stops,
- closes anything the broker no longer has,
- flags and squares off anything unexpected,
- never sends a duplicate order.

You will see a `recovery` line in the output. Nothing manual is required.

---

# PART 3 — LIVE MODE

## 13. Before you go live

- Paper mode has run cleanly for at least one full session.
- `scripts\smartapi_login_check.py` passes.
- You have completed the supervised smoke test in
  `docs\FINAL_OPEN_ITEMS.md` §2 (**mandatory** — the live order path has never
  been exercised against the real broker).

## 14. Which configuration values change

Create `live.json`:

```json
{
  "mode": "live",
  "live_trading_enabled": true,
  "symbols_file": "nifty100.txt",
  "timeframes": ["15m"],
  "capital": {
    "deploy_today": 50000,
    "max_daily_loss": 2000
  },
  "risk": { "max_open_positions": 2 }
}
```

**Start small.** ₹50,000 deployed means at most ₹25,000 in any one trade
and at most ₹2,000 at risk on it — deliberately far below paper.

## 15. The command that starts live trading

Live needs **three keys at once**. Two are in the file above; the third is typed
into the terminal each session **on purpose**:

```
$env:ALGO_ENABLE_LIVE="YES"
.venv\Scripts\python scripts\run_trading.py --config live.json --loop --serve-dashboard
```

Confirm the first line reads:

```
mode=live  live_armed=True
```

The dashboard shows a red **LIVE TRADING ARMED** banner.

> **Never** put `ALGO_ENABLE_LIVE` in a permanent profile. Without it, real
> orders are impossible even if the config says live.

## 16. How to stop live trading safely

1. **Ctrl-C** — stops the engine. Open positions stay open at the broker.
2. To flatten first, create the kill file, wait one cycle, then Ctrl-C:
   ```
   echo stop > user_data\trading\KILL
   ```
3. Always confirm at your broker terminal that you are flat.
4. To disarm for good: close the terminal (the env variable disappears) or set
   `"live_trading_enabled": false`.

---

# PART 4 — WHERE EVERYTHING LIVES

## 17. Files updated while running

| File | Contains | Updated |
|---|---|---|
| `user_data\trading\portfolio.json` | positions, orders, P&L | after every change |
| `user_data\trading\events-YYYYMMDD.jsonl` | full audit trail | every event |
| `user_data\trading\summary-YYYY-MM-DD.json` | end-of-day summary | at shutdown |
| `dashboard\dashboard_data\*.json` | the 10 dashboard files | every cycle |
| `user_data\data\nse\` | candle store | when data refreshes |

## 18. How often the dashboard refreshes

- The engine **writes** the JSON files at the end of every cycle.
- The browser **re-reads** them every **2 seconds**.
- If the newest snapshot is older than **45 seconds**, the dashboard shows a red
  **STALE DATA** banner — that means the engine has stopped or hung.

## 19. Where logs are written

- **Event log (primary):** `user_data\trading\events-YYYYMMDD.jsonl` — one JSON
  line per event. This answers "why did/didn't it trade X".
- **Console:** the VS Code terminal, live.
- **Dashboard:** the ENGINE LOG panel, searchable.

## 20. Refreshing market data

```
.venv\Scripts\python scripts\download_history.py --symbols-file nifty100.txt --timeframes 15m
```

---

# PART 5 — REVIEWING WHAT HAPPENED

## 21. Completed trades
Dashboard → **COMPLETED TRADES** (symbol, strategy, entry, exit, quantity, exit
reason, P&L). Permanent copy: `summary-YYYY-MM-DD.json`.

## 22. Rejected trades
Dashboard → **REJECTED** panel, with the exact reason
(e.g. *"symbol already held"*, *"no capital left: 300,000 of 300,000
deployed"*, or *"max_open_positions (5)"*). This is the most
useful panel for understanding why the system passed on something.

## 23. Scanner activity
Dashboard → **SCANNER** panel: how many stocks and strategies are being
evaluated, what it is doing right now, and the countdown to the next scan.

## 24. Portfolio performance
Dashboard → **PORTFOLIO** (capital, exposure, plus the risk-budget row:
portfolio open risk, reserved pending risk, remaining budget and risk
utilisation %) and **TODAY'S PERFORMANCE** (trades, win rate, P&L, largest
win/loss).

## 25. Verifying the dashboard has fresh data
Top-right shows `data age Ns`. Under ~10s is normal. The footer shows the
snapshot time. A red STALE banner means the engine stopped.

## 26. If the dashboard stops updating

| Check | How |
|---|---|
| Is the engine running? | Look at the VS Code terminal — new lines appearing? |
| Is the page served correctly? | The URL must be `http://localhost:8787/`, **not** a `file:///...` path |
| Are files being written? | `dir dashboard\dashboard_data` — check timestamps |
| Browser cache | Press **Ctrl-F5** to hard-refresh |
| Port conflict | Restart with `--dashboard-port 8788` |

---

# PART 6 — END OF DAY & NEXT DAY

## 27. End-of-day shutdown

1. Confirm no open positions (dashboard shows `open=0`), or square off with the
   kill file.
2. Press **Ctrl-C** in the terminal.
3. Confirm `daily summary written: ...`.
4. Review the summary file and the dashboard's completed trades.

## 28. Starting the next trading day

1. Open VS Code at the project folder.
2. (Optional) refresh data — §20.
3. Run the same start command — §8.
4. Check the four startup items — §9.

## 29. The complete daily workflow

```
Open VS Code  (folder: C:\Projects\Algo Trading\Algo Trader)
        ↓
Terminal → New Terminal
        ↓
.venv\Scripts\python scripts\run_trading.py --mode paper --loop --serve-dashboard
        ↓
Confirm: mode=paper, PREFLIGHT PASSED, risk values correct
        ↓
Open browser → http://localhost:8787   (or the LAN address on your phone)
        ↓
Monitor: system bar green · scanner active · trades appear in ACTIVE TRADES
        ↓
Review: COMPLETED TRADES · REJECTED · TIMELINE
        ↓
End of day: confirm flat → Ctrl-C → "daily summary written"
```

## 30. Quick reference card

| I want to… | Do this |
|---|---|
| Start (paper) | `.venv\Scripts\python scripts\run_trading.py --mode paper --loop --serve-dashboard` |
| Open the dashboard (laptop) | browser → `http://localhost:8787` |
| Open the dashboard (phone) | the LAN address printed at startup, e.g. `http://192.168.1.14:8787` |
| Restrict dashboard to laptop | add `--dashboard-host 127.0.0.1` |
| Stop | **Ctrl-C** in the terminal |
| Emergency flatten | `echo stop > user_data\trading\KILL` |
| Resume after emergency | `del user_data\trading\KILL`, then restart |
| See why a trade was skipped | dashboard → REJECTED panel |
| See today's P&L | dashboard → TODAY'S PERFORMANCE |
| Check broker login | `.venv\Scripts\python scripts\smartapi_login_check.py` |
| Refresh data | `.venv\Scripts\python scripts\download_history.py --symbols-file nifty100.txt --timeframes 15m` |
| Go live | see §14–15 (three keys required) |

---

## Golden rules

1. **Paper first.** Live only after the smoke test in `FINAL_OPEN_ITEMS.md`.
2. **Read the preflight line** — it shows the money settings actually in use.
3. **The dashboard cannot control anything.** Stopping means Ctrl-C in the
   terminal or the kill file. Closing the browser does nothing.
4. **Never run two engines at once** — both would manage the same positions.
5. **If anything looks wrong, Ctrl-C.** State is always saved; restarting is safe.
