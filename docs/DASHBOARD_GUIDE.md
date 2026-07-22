# DASHBOARD GUIDE

_The read-only monitoring terminal: what it shows, how the data gets there, and
how to host it. Operator start-up instructions live in `OPERATOR_MANUAL.md`._

## 1. What it is (and what it is not)

A **static web page** (`dashboard/index.html` + `styles.css` + `app.js`) that
reads JSON snapshots the trading engine writes. There is **no backend, no
operator API, and no control channel**.

**It cannot** start, stop, pause, resume, modify strategies, or change any
setting. The only way to control the engine is the terminal (Ctrl-C) or the
kill-switch file. Closing the browser has no effect on trading.

**Design goal:** a professional trading-desk terminal — dense, dark, monospace,
colour-coded — where the operator sees at a glance: *is it running, what is it
doing, why did it enter, what is it waiting for, what happens next, what
happened today.*

## 2. How data gets there

```
ProductionEngine.run_cycle()
        ↓  (end of every cycle)
DashboardExporter.export()        src/algo/trading/dashboard.py
        ↓  atomic writes (temp file + replace)
         dashboard/dashboard_data/*.json   12 files
         ↓  live.json every second; the remaining snapshots every 5 seconds
dashboard/index.html              the browser
```

The exporter is **observational only**: it reads engine state and writes files.
It never places an order or influences a decision, and every failure inside it
is caught and logged so a dashboard problem can never affect trading.

Writes are atomic (temp + `os.replace`), so the browser never reads a
half-written file.

## 3. The twelve files

| File | Contents |
|---|---|
| `system.json` | engine running, paper/live, market open, IST clock, broker, data feed, scanner/scheduler/recovery status, halt reason |
| `scanner.json` | universe size/tier/target, registered + scanning strategy counts, headline, current activity, next-scan countdown, session time remaining, activity log |
| `positions.json` | every active trade: prices, stop, T1/T2, P&L, state, **next planned action**, **full explainability** |
| `orders.json` | recent order lifecycle (side, intent, qty, fill, status, broker id) |
| `portfolio.json` | `deploy_today` and its derived caps (`max_per_trade`), deployed, available, exposure %, realized/unrealized, and the portfolio risk budget (`portfolio_open_risk`, `reserved_pending_risk`, `remaining_risk_budget`, `risk_utilization_pct`) |
| `signals.json` | potential opportunities + rejected ones with the exact reason, waiting condition |
| `timeline.json` | chronological session feed (colour-coded by kind) |
| `performance.json` | today's trades, wins/losses, win rate, P&L, largest win/loss, open risk, completed trades |
| `health.json` | component statuses, CPU/RAM (if `psutil` installed), cycles, errors, circuit-breaker state |
| `logs.json` | latest engine messages for the searchable table |

Every file has the shape
`{"generated_at": "<UTC ISO>", "schema": <int>, "data": {...}}`.
`generated_at` drives the staleness detector; `schema` lets the UI refuse to
render a snapshot left by an older build (which would otherwise display
superseded figures as if they were live).

**All capital and risk figures come from `AccountRiskEngine.risk_state` — the
same call the engine uses for sizing and the entry gate.** The exporter never
recomputes capital independently, so the dashboard cannot disagree with the
engine.

**All displayed times are IST**, formatted by the engine, so the dashboard
never mixes timezones.

## 4. Panels and responsive layout

| Panel | Answers |
|---|---|
| **Top status bar** | Engine · Mode · Market · Broker · Data Feed · Scanner · Scheduler · Recovery, plus the IST clock and data age |
| **Market overview** | session, time remaining, **universe size**, **registered strategies**, **scanning strategies**, next scan, square-off time |
| **Scanner** | *"Looking for opportunities across 99 stocks using 12 strategies."* + what it is evaluating right now |
| **Portfolio** | total/realized/unrealized P&L, deployed, available, exposure, positions used, plus the portfolio risk budget: open risk, reserved pending risk, remaining budget, utilisation % |
| **Today's performance** | trades, wins, losses, win rate, P&L, largest win/loss, open risk |
| **Active trades** | per trade: entry, current, stop, T1, T2, live P&L, current state, **next planned action**, and an expandable **why this trade was taken** |
| **Opportunities / Rejected** | signals with confidence; rejections with the exact risk reason |
| **Session timeline** | chronological feed, colour-coded (signal, order, position, reject, error) |
| **System health** | CPU, RAM, cycles, errors, per-component status, risk-trip reason |
| **Completed trades** | today's closed trades with exit reason and P&L |
| **Orders** | order audit trail |
| **Engine log** | searchable message table |

The desktop view uses a dense operations grid: overview/data, scanner/health,
portfolio/performance, active trades, opportunities/rejections,
timeline/completed trades, orders, and logs. On narrow screens it becomes a
single-column view with two-column metric cards and horizontally scrollable
tables. Secondary detail remains available through the active-trade
explainability disclosure and the bounded table scroll regions; no operational
field is removed.

### Explainability (the "why")

Each active trade expands to show: entry trigger (the strategy's actual rule),
volume confirmation, trend confirmation, risk approval (₹ at risk vs the remaining portfolio risk budget),
capital approval (vs half the day's capital), duplicate-position check, daily-loss check, and a
plain-English decision statement.

Because the position exists, every pre-trade gate necessarily approved it —
each line reports the concrete limit it was checked against.

### Next planned action

Derived from the position's own frozen `ExecutionSpec`, e.g.:

- *"Book 50% at ₹4,088.00, then move the stop to breakeven (₹4,020.00) and run the rest to ₹4,140.00."*
- *"Trail the stop upward by 2×ATR once in profit; exit on the trailed stop or square off at 15:15."*
- *"Exit at target ₹3,145.00 or stop ₹3,098.00; square off at 15:15."*

## 5. Notifications

Tick **notifications** in the ENGINE LOG panel header and allow the browser
prompt. You then get desktop notifications for: trade opened, trade closed,
target hit, stop hit, system errors, and stale data (engine stopped).

History is never replayed — only events that occur after you enable it.

## 6. Staleness detection

The dashboard compares `generated_at` with your clock:

- **< 45s** — normal; header shows `data age Ns`.
- **> 45s** — red **STALE DATA** banner: the engine has stopped or hung.
- **No files at all** — **NO DATA** banner with what to check.

## 7. Hosting

### 7a. Recommended: let the engine serve it (one command)

```
.venv\Scripts\python scripts\run_trading.py --mode paper --loop --serve-dashboard
```

Serves `dashboard/` on port **8787** from a background daemon thread, bound to
`0.0.0.0` so **any device on the same Wi-Fi** (your phone) can open it. The
console prints every usable address and marks the one to use:

```
  Dashboard available at:
    http://localhost:8787             (this laptop)
    http://192.168.1.14:8787          (most likely - this machine's active network connection)  <-- open this on your phone
    http://192.168.240.1:8787         (probably a virtual adapter (WSL/Hyper-V/Docker) - phones cannot reach this)
```

| Flag | Effect |
|---|---|
| `--dashboard-port 8788` | different port |
| `--dashboard-host 127.0.0.1` | laptop only (no phone access) |
| `--no-qr` | suppress the QR code |

**How the address is chosen.** `algo/trading/network.py` opens a UDP socket
toward a public IP (sending nothing — it is a routing-table lookup, so it works
with no internet) to find the interface holding the **default route**; that is
the address a phone can reach. Other private addresses are listed as
alternatives, and any address ending in `.1` that is not the route address is
flagged as a probable virtual switch (WSL/Hyper-V/Docker/VirtualBox). Loopback
(127/8) and link-local/APIPA (169.254/16) are never offered. If nothing is
found it says so instead of guessing.

**QR code (optional).** If `qrcode` (or `segno`) is installed, a scannable QR
of the phone address is printed. It is drawn with ANSI background colours on
plain spaces — not block glyphs — because Windows consoles are cp1252 and
would raise `UnicodeEncodeError` on block characters. QR rendering is wrapped
so it can never interfere with starting the engine. Install with
`pip install qrcode`; nothing requires it.

### 7b. Standalone static server

```
.venv\Scripts\python -m http.server 8787 --directory dashboard --bind 0.0.0.0
```

Then open `http://localhost:8787` (or the LAN address on a phone).

> **Do not open `index.html` by double-clicking it.** Browsers block `fetch()`
> from `file://` pages, so the panels would stay empty. It must be *served*.

### 7c. Using an existing website / remote hosting

The dashboard is plain static files, so any web host works — copy `index.html`,
`styles.css`, `app.js` into a folder on your site and keep `dashboard_data/`
**as a sub-folder next to them** (`app.js` fetches the relative path
`dashboard_data/…`).

To publish from the trading machine, point the engine's export directory at the
site folder:

```json
{ "dashboard_dir": "C:/inetpub/wwwroot/algo/dashboard_data" }
```

or sync `dashboard/dashboard_data/` to the host on a schedule.

**Security.**

*What cannot happen:* the server answers **GET/HEAD only** — POST, PUT, PATCH
and DELETE are refused with `405` — it serves the `dashboard` folder alone
(path traversal returns 404), and there is **no endpoint that can place,
modify or cancel an order, change configuration, restart the engine or stop
trading**. Nothing a browser does can affect trading. Verified by request
tests.

*What can happen:* anyone on the network can **read** it — positions, P&L and
risk limits — because there is no authentication. Guidance:

| Network | Recommendation |
|---|---|
| Home / private Wi-Fi | Default (`0.0.0.0`) is fine — that is the intended phone workflow |
| Shared or public Wi-Fi (café, hotel, office) | Start with `--dashboard-host 127.0.0.1` |
| The internet | **Don't.** Never port-forward this. If remote access is truly needed, put it behind a VPN with HTTPS and authentication |

Windows may prompt to allow Python through the firewall on first run — allow
**Private networks** only, never Public.

## 8. Configuration

| Setting | Default | Meaning |
|---|---|---|
| `dashboard_enabled` | `true` | set `false` to switch exporting off entirely |
| `dashboard_dir` | `dashboard/dashboard_data` | where snapshots are written |
| `--serve-dashboard` | off | serve the UI from the engine process |
| `--dashboard-host` | `0.0.0.0` | `0.0.0.0` = reachable from phones on the same Wi-Fi; `127.0.0.1` = this laptop only |
| `--no-qr` | off | suppress the startup QR code |
| `--dashboard-port` | `8787` | port for the built-in server |

Client-side constants at the top of `app.js`: `POLL_FAST_MS` (1000),
`POLL_SLOW_MS` (5000), and `STALE_SECONDS` (45). Polling pauses while the
browser tab is hidden and refreshes immediately when it becomes visible again.
Snapshot JSON is compactly serialized; the schema and field names are
unchanged.

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| All panels empty, **NO DATA** | opened via `file://`, or engine not running | serve it (§7); start the engine |
| **STALE DATA** banner | engine stopped/hung | check the terminal; restart |
| CPU/RAM show `n/a` | `psutil` not installed | `pip install psutil` (optional) |
| Times look wrong | stale cached `app.js` | hard-refresh with **Ctrl-F5** |
| Port already in use | another server running | `--dashboard-port 8788` |
| Phone cannot connect | firewall / wrong address / different Wi-Fi | allow `python.exe` on Private networks; use the address marked *open this on your phone*; confirm both devices are on the same Wi-Fi (see OPERATOR_MANUAL §8b) |
| Only virtual-adapter addresses listed | Wi-Fi/Ethernet down | reconnect the network and restart |
| Notifications don't appear | permission not granted | tick the box, allow the browser prompt |
