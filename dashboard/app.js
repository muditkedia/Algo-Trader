/* ALGO TRADER monitoring terminal — READ-ONLY.
 *
 * Polls the JSON snapshots the trading engine exports into dashboard_data/
 * and renders them. It sends nothing back: there is no control channel, no
 * write path, and no engine API. If the engine stops, this page simply goes
 * stale (and says so).
 */
"use strict";

const DATA_DIR = "dashboard_data";

/* Two refresh tiers.
 *
 * The fast tier is one small file (live.json) holding only what genuinely
 * changes second to second: prices, P&L, position values, portfolio value and
 * the countdowns. The slow tier is everything driven by a completed candle —
 * scanner state, signals, explainability, timeline, logs — which cannot change
 * between bars and is wasteful (and visually noisy) to redraw every second.
 *
 * Redraws are minimised the same way: position CARDS are rebuilt only when the
 * set of positions or their state changes, while the numbers inside them are
 * patched in place on every fast tick. Rebuilding the cards each second closed
 * any open "why this trade" panel and made the page flicker.
 */
const POLL_FAST_MS = 1000;     // live.json: prices, P&L, countdowns
const POLL_SLOW_MS = 5000;     // everything a completed candle changes
const STALE_SECONDS = 45;      // older than this ⇒ the engine looks stopped
const SCHEMA_VERSION = 2;      // must match algo.trading.dashboard.SCHEMA_VERSION

const FAST_FILES = ["live"];
const SLOW_FILES = ["system", "scanner", "marketdata", "positions", "orders",
                    "portfolio", "signals", "timeline", "performance",
                    "health", "logs"];
const FILES = FAST_FILES.concat(SLOW_FILES);

const state = {};              // last good payload per file
let logFilter = "";
let notifyOn = false;
const seen = { positions: new Set(), closed: new Set(), notified: new Set() };
let firstLoad = true;
let cardsKey = "";             // signature of the rendered position cards
let localTick = null;          // {at, timing} for smooth countdowns between polls
let slowRefreshInFlight = false;
let fastRefreshInFlight = false;

/* ------------------------------------------------------------ utilities */
const $ = (id) => document.getElementById(id);
const money = (v) => (v === null || v === undefined || isNaN(v)) ? "—"
  : "₹" + Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 });
const signed = (v) => (v === null || v === undefined || isNaN(v)) ? "—"
  : (v >= 0 ? "+" : "") + money(v).replace("₹", "₹");
const pct = (v) => (v === null || v === undefined || isNaN(v)) ? "—"
  : Number(v).toFixed(1) + "%";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cls = (v) => v > 0 ? "pos" : (v < 0 ? "neg" : "");

function mmss(sec) {
  if (sec === null || sec === undefined || isNaN(sec)) return "—";
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60),
        s = sec % 60;
  return h ? `${h}h ${m}m` : (m ? `${m}m ${String(s).padStart(2, "0")}s`
                                : `${s}s`);
}

/* -------------------------------------------------------------- polling */
async function pull(name) {
  try {
    const res = await fetch(`${DATA_DIR}/${name}.json?t=${Date.now()}`,
                            { cache: "no-store" });
    if (!res.ok) return null;
    const body = await res.json();
    state[name] = body;
    return body;
  } catch (e) { return null; }          // engine not running / not served
}

async function refreshSlow() {
  if (document.hidden || slowRefreshInFlight) return;
  slowRefreshInFlight = true;
  try {
  const results = await Promise.all(SLOW_FILES.map(pull));
  const anyData = results.some(r => r !== null) || state.live !== undefined;
  render(anyData);
  firstLoad = false;
  } finally {
    slowRefreshInFlight = false;
  }
}

async function refreshFast() {
  if (document.hidden || fastRefreshInFlight) return;
  fastRefreshInFlight = true;
  try {
  const body = await pull("live");
  if (body?.data?.timing) localTick = { at: Date.now(), timing: body.data.timing };
  renderLive();
  } finally {
    fastRefreshInFlight = false;
  }
}

/* ------------------------------------------------------------ rendering */
function render(anyData) {
  const sys = state.system?.data;
  const generated = state.system?.generated_at;
  const ageSec = generated ? (Date.now() - new Date(generated).getTime()) / 1000
                           : Infinity;
  const stale = ageSec > STALE_SECONDS;

  /* Each section renders INDEPENDENTLY. Previously they ran as one sequence,
   * so a single failure stopped every section after it and left blank panels
   * with no console message — the operator could not tell "no data" from
   * "renderer broke". A failure now costs one panel, and says so. */
  const sections = [
    ["connection", () => renderConnection(anyData, stale, ageSec)],
    ["system", () => sys && renderSystem(sys, stale)],
    ["scanner", renderScanner],
    ["marketdata", renderMarketData],
    ["portfolio", renderPortfolio],
    ["performance", renderPerformance],
    ["positions", renderPositions],
    ["signals", renderSignals],
    ["timeline", renderTimeline],
    ["health", renderHealth],
    ["closed", renderClosed],
    ["orders", renderOrders],
    ["logs", renderLogs],
  ];
  for (const [name, fn] of sections) {
    try { fn(); }
    catch (e) { console.error(`dashboard: ${name} panel failed to render`, e); }
  }

  $("foot-updated").textContent = generated
    ? `snapshot ${new Date(generated).toLocaleTimeString()} · refreshed ${new Date().toLocaleTimeString()}`
    : "no snapshot found";
}

/* ---------------------------------------------------------- fast tier */

/* Countdown that keeps ticking between polls: the engine's value plus the
 * time elapsed since we received it. */
function countdown(seconds) {
  if (seconds === null || seconds === undefined) return null;
  const elapsed = localTick ? (Date.now() - localTick.at) / 1000 : 0;
  return Math.max(0, Math.round(seconds - elapsed));
}

function renderLive() {
  const d = state.live?.data; if (!d) return;
  const t = d.timing || {};

  /* MARKET STATUS — replaces the wall clock. The operator's own screen shows
   * the time; what it cannot show is when the scanner next acts. */
  setPill("s-market", t.status || "—", t.market_open ? "ok" : "");
  const tfs = t.timeframes || [];
  const primary = tfs[0];
  const nextBar = primary ? countdown((t.next_candle || {})[primary]) : null;
  $("s-clock").textContent = nextBar === null ? "—" : mmss(nextBar);
  $("s-clocklabel").textContent = primary ? `${primary} CANDLE CLOSES IN` : "NEXT CANDLE";

  const sq = countdown(t.seconds_to_squareoff);
  setText("m-squareoff", t.squareoff_time
    ? `${t.squareoff_time}${sq !== null && t.market_open ? " · in " + mmss(sq) : ""}`
    : "—");
  setText("m-remaining", t.session_seconds_remaining
    ? mmss(countdown(t.session_seconds_remaining)) : "market closed");
  setText("m-nextcandle", tfs.length
    ? tfs.map(tf => `${tf} ${mmss(countdown((t.next_candle || {})[tf]))}`).join(" · ")
    : "—");

  /* DATA AGE — the age of the CANDLES, never the age of the export. */
  const age = $("s-feedage");
  const stale = d.data_status === "STALE";
  const src = d.quotes_live ? "LTP" : "candle";
  age.textContent = d.data_age_seconds === null || d.data_age_seconds === undefined
    ? (d.data_status || "no data")
    : `${d.data_status} · ${src} · candle ${mmss(d.data_age_seconds)} old`;
  age.className = "feedage" + (stale ? " stale" : "");

  /* live numbers */
  setText("p-total", signed(d.total_pnl), cls(d.total_pnl));
  setText("p-realized", signed(d.realized_pnl), cls(d.realized_pnl));
  setText("p-unreal", signed(d.unrealized_pnl), cls(d.unrealized_pnl));
  setText("p-deployed", money(d.deployed_capital));
  setText("p-available", money(d.available_capital));
  setText("p-risk", money(d.open_risk));
  setText("p-value", money(d.portfolio_value));

  /* patch the numbers inside each position card WITHOUT rebuilding it */
  (d.positions || []).forEach(p => {
    const card = document.querySelector(`[data-pos="${cssEscape(p.position_id)}"]`);
    if (!card) return;
    const put = (sel, text, klass) => {
      const el = card.querySelector(sel); if (!el) return;
      if (el.textContent !== text) el.textContent = text;
      if (klass !== undefined) el.className = klass;
    };
    put(".js-current", money(p.current_price));
    put(".js-pnl", `${signed(p.pnl)} (${pct(p.pnl_pct)})`, "pnl " + cls(p.pnl));
    put(".js-value", money(p.value));
    put(".js-src", p.price_source === "LTP" ? "live" : "candle close");
    const sqEl = card.querySelector(".js-squareoff");
    if (sqEl && sq !== null) sqEl.textContent = mmss(sq);
    card.className = "trade " + (p.pnl > 0 ? "up" : (p.pnl < 0 ? "down" : ""));
  });
}

function setText(id, text, klass) {
  const el = $(id); if (!el) return;
  if (el.textContent !== text) el.textContent = text;
  if (klass !== undefined) el.className = "num" + (klass ? " " + klass : "");
}

const cssEscape = (s) => String(s).replace(/["\\]/g, "\\$&");

function renderMarketData() {
  const m = state.marketdata?.data; if (!m) return;
  const set = (id, v, bad) => {
    const el = $(id); if (!el) return;
    el.textContent = v;
    el.className = "num" + (bad ? " neg" : "");
  };
  set("md-universe", m.configured_universe);
  set("md-resolved", `${m.resolved_symbols} / ${m.configured_universe}`,
      m.mapping_applicable && m.resolved_symbols < m.configured_universe);
  set("md-live", m.live_symbols);
  set("md-fresh", `${m.fresh_symbols} / ${m.configured_universe}`,
      m.stale_symbols > 0);
  set("md-missing", m.missing_symbols, m.missing_symbols > 0);
  set("md-failed", m.failed_fetches, m.failed_fetches > 0);
  set("md-lastupdate", m.last_successful_update_ist || "never",
      !m.last_successful_update);
  set("md-status", m.feed_status,
      m.feed_status !== "FRESH" && m.feed_status !== "MARKET CLOSED");
  const detail = $("md-detail");
  if (detail) {
    const lines = [esc(m.headline || "")];
    if (m.stale_examples?.length)
      lines.push(`Stale: ${esc(m.stale_examples.join(", "))}`);
    if (m.missing_examples?.length)
      lines.push(`No data: ${esc(m.missing_examples.join(", "))}`);
    detail.innerHTML = lines.filter(Boolean).join("<br>") || "—";
    detail.className = "datadetail" + (m.feed_status === "STALE"
      || m.failed_fetches ? " bad" : "");
  }
}

function renderConnection(anyData, stale, ageSec) {
  const banner = $("banner"), age = $("s-feedage");
  if (!anyData) {
    banner.className = "banner";
    banner.textContent = "NO DATA — the dashboard cannot find dashboard_data/. "
      + "Is the trading engine running, and is this page served from the "
      + "dashboard folder?";
    age.textContent = "no data"; age.className = "feedage stale";
    setPill("s-engine", "OFFLINE", "bad");
    return;
  }
  const schema = state.system?.schema;
  if (schema !== undefined && schema !== SCHEMA_VERSION) {
    banner.className = "banner";
    banner.textContent = `INCOMPATIBLE SNAPSHOT — dashboard_data was written by `
      + `a different build (schema ${schema} vs ${SCHEMA_VERSION}). `
      + `These figures are NOT current: restart the engine to refresh them.`;
    age.textContent = "stale schema"; age.className = "feedage stale";
    setPill("s-engine", "SCHEMA MISMATCH", "bad");
    return;
  }
  age.textContent = isFinite(ageSec) ? `data age ${Math.round(ageSec)}s`
                                     : "unknown";
  age.className = "feedage" + (stale ? " stale" : "");
  if (stale) {
    banner.className = "banner";
    banner.textContent = `STALE DATA — last snapshot ${Math.round(ageSec)}s ago. `
      + "The engine may have stopped.";
    notify("Engine data stale", "No fresh snapshot from the trading engine.",
           "stale-" + Math.floor(Date.now() / 60000));
    return;
  }
  const sys = state.system?.data;
  if (sys?.trading_halted) {
    banner.className = "banner";
    banner.textContent = "TRADING HALTED — " + (sys.halt_reason || "risk stop engaged")
      + ". Positions are still being managed; no new entries will be taken.";
  } else if (sys?.mode === "live" && sys?.live_armed) {
    banner.className = "banner warn";
    banner.textContent = "LIVE TRADING ARMED — real orders are being placed.";
  } else {
    banner.className = "banner hidden";
  }
}

function setPill(id, text, klass) {
  const el = $(id); if (!el) return;
  el.textContent = text; el.className = "pill " + (klass || "");
}

function renderSystem(sys, stale) {
  setPill("s-engine", stale ? "STALE" : (sys.trading_halted ? "HALTED" : "RUNNING"),
          stale ? "bad" : (sys.trading_halted ? "warn" : "ok"));
  setPill("s-mode", sys.mode === "live" ? (sys.live_armed ? "LIVE ARMED" : "LIVE (disarmed)") : "PAPER",
          sys.mode === "live" ? (sys.live_armed ? "live" : "warn") : "paper");
  setPill("s-market", sys.market_status, sys.market_open ? "ok" : "");
  setPill("s-broker", `${(sys.broker?.name || "—").toUpperCase()} ${sys.broker?.connected ? "✓" : "…"}`,
          sys.broker?.connected ? "ok" : "warn");
  // the feed pill reports whether candles are ARRIVING, not merely whether a
  // watchlist exists — a store that stopped updating still serves prices
  const feedState = sys.data_feed_status || (sys.data_feed_connected ? "OK" : "NO DATA");
  setPill("s-feed",
          feedState === "OK" ? "CONNECTED"
            : (feedState === "OFFLINE" ? "OFFLINE (stored)" : feedState),
          feedState === "OK" ? "ok" : (feedState === "OFFLINE" ? "warn" : "bad"));
  setPill("s-scanner", sys.scanner_status,
          sys.scanner_status === "SCANNING" ? "ok" :
          (sys.scanner_status === "HALTED" ? "bad" : ""));
  setPill("s-sched", sys.scheduler_status, sys.scheduler_status === "ACTIVE" ? "ok" : "");
  setPill("s-recovery", sys.recovery_status, "ok");
  // the header slot now shows the NEXT CANDLE countdown, owned by the fast
  // tier (renderLive) — a wall clock duplicates what the operator already has
  $("m-session").textContent = sys.session_date || "—";
}

/* Token mapping + market-data state.
 *
 * These two answer "is market data working?" without reading a log. Mapping
 * is the precondition (a symbol with no instrument token can never receive a
 * candle); market data is the outcome, and it distinguishes "no new candles"
 * from "unable to fetch candles" so an idle market never looks like a fault
 * and a fault never looks idle.
 */
function renderDiagnostics(sc) {
  const map = sc.token_mapping, md = sc.market_data;
  const el = $("m-mapping");
  if (el && map) {
    const bad = map.applicable && map.unresolved > 0;
    el.textContent = map.applicable
      ? `${map.resolved} / ${map.total} resolved`
      : "n/a (offline)";
    el.className = "num" + (bad ? " neg" : (map.applicable ? " pos" : ""));
    el.title = map.summary || "";
  }
  const ds = $("m-datastate");
  if (ds && md) {
    ds.textContent = md.status || "—";
    ds.className = "num" + (md.status === "OK" ? " pos"
      : (md.status === "UNABLE TO FETCH" ? " neg" : ""));
  }
  const detail = $("m-datadetail");
  if (!detail) return;
  const lines = [];
  if (md?.headline) lines.push(esc(md.headline));
  if (map?.applicable && map.unresolved > 0) {
    lines.push(`Unresolved symbols (${map.unresolved}): `
      + esc((map.examples || []).join(", "))
      + (map.unresolved > (map.examples || []).length ? " …" : ""));
  }
  detail.innerHTML = lines.length ? lines.join("<br>") : "—";
  detail.className = "datadetail"
    + ((md?.status === "UNABLE TO FETCH" || map?.unresolved > 0) ? " bad" : "");
}

function renderScanner() {
  const sc = state.scanner?.data; if (!sc) return;
  renderDiagnostics(sc);
  setText("sc-headline", sc.headline || "—");
  setText("sc-current", sc.current_activity || "—");
  setText("m-watchlist", sc.universe_size ?? sc.watchlist_size ?? "—");
  setText("m-registered", sc.registered_strategies ?? "—");
  setText("m-strategies", sc.scanning_strategies ?? sc.strategies_enabled ?? "—");
  // NOTE: m-remaining, m-squareoff and m-nextcandle are owned by the FAST
  // tier (renderLive) - the countdowns must tick every second, not every scan.
  const act = sc.activity || [];
  const box = $("sc-activity");
  if (box) box.innerHTML = act.length
    ? act.map(a => `<div><span>${esc(a.time)}</span>${esc(a.message)}${a.detail ? " — " + esc(a.detail) : ""}</div>`).join("")
    : `<div>Waiting for the next completed candle.</div>`;
}

function renderPortfolio() {
  const p = state.portfolio?.data; if (!p) return;
  const set = (id, val, klass) => {
    const el = $(id); el.textContent = val;
    el.className = "num" + (klass ? " " + klass : "");
  };
  // P&L / capital figures are owned by the FAST tier (live.json) so the two
  // tiers cannot show different numbers for the same quantity. They are filled
  // in here only as a fallback, when live.json has not arrived yet.
  if (!state.live?.data) {
    set("p-total", signed(p.total_pnl), cls(p.total_pnl));
    set("p-realized", signed(p.realized_pnl), cls(p.realized_pnl));
    set("p-unreal", signed(p.unrealized_pnl), cls(p.unrealized_pnl));
    set("p-risk", money(p.open_risk));
    set("p-deployed", money(p.deployed_capital));
    set("p-available", money(p.available_capital));
  }
  set("p-exposure", pct(p.exposure_pct));
  set("p-positions", `${p.open_positions}/${p.max_open_positions}`);
  // portfolio risk budget (read-only, like everything else here)
  set("r-open", money(p.portfolio_open_risk));
  set("r-reserved", money(p.reserved_pending_risk));
  const util = p.risk_utilization_pct;
  set("r-remaining", money(p.remaining_risk_budget),
      p.remaining_risk_budget <= 0 ? "neg" : "");
  set("r-util", pct(util), util >= 90 ? "neg" : (util >= 70 ? "" : "pos"));
}

function renderPerformance() {
  const f = state.performance?.data; if (!f) return;
  $("f-trades").textContent = f.trades;
  $("f-wins").textContent = f.wins;
  $("f-losses").textContent = f.losses;
  $("f-winrate").textContent = pct(f.win_rate);
  const pnl = $("f-pnl");
  pnl.textContent = signed(f.pnl); pnl.className = "num " + cls(f.pnl);
  $("f-bigwin").textContent = money(f.largest_win);
  $("f-bigloss").textContent = money(f.largest_loss);
  $("f-risk").textContent = money(f.open_risk);
}

function renderPositions() {
  const d = state.positions?.data; if (!d) return;
  $("pos-count").textContent = d.count;
  const box = $("positions");
  if (!d.positions.length) {
    box.innerHTML = `<div class="empty">No open positions.</div>`;
    seen.positions.clear();
    cardsKey = "";
    return;
  }

  /* Rebuild only when the STRUCTURE changes — a new position, a partial
   * booked, a trail engaged, a level moved. The per-second numbers are patched
   * in place by renderLive, so an open "why this trade" panel survives and the
   * page does not flicker. */
  const key = d.positions.map(p => [p.position_id, p.partial_done, p.trailing,
                                    p.stop_loss, p.target_1, p.target_2,
                                    p.state].join("~")).join("|");
  if (key === cardsKey) return;
  cardsKey = key;

  box.innerHTML = d.positions.map(p => {
    const w = p.explainability || {};
    const plan = p.execution_plan || {};
    const triggers = (p.triggers || []).map(t => `
        <div class="trigger ${esc(t.kind)}">
          <span class="cond">IF ${esc(t.condition)}</span>
          <span class="act">${esc(t.action)}</span>
        </div>`).join("");
    return `
    <div class="trade ${p.pnl > 0 ? "up" : (p.pnl < 0 ? "down" : "")}" data-pos="${esc(p.position_id)}">
      <div class="th">
        <span class="sym">${esc(p.symbol)}</span>
        <span class="direction ${p.direction === "short" ? "short" : "long"}">${p.direction === "short" ? "SHORT" : "LONG"}</span>
        <span class="strat">${esc(p.strategy)}</span>
        <span class="js-pnl pnl ${cls(p.pnl)}">${signed(p.pnl)} (${pct(p.pnl_pct)})</span>
      </div>
      <div class="levels">
        <div><label>ENTRY</label><b>${money(p.entry_price)}</b></div>
        <div><label>CURRENT <em class="js-src src">${p.price_source === "LTP" ? "live" : "candle close"}</em></label><b class="js-current">${money(p.current_price)}</b></div>
        <div><label>STOP</label><b class="neg">${money(p.stop_loss)}</b></div>
        <div><label>TARGET 1</label><b class="pos">${p.target_1 ? money(p.target_1) : "—"}</b></div>
        <div><label>TARGET 2</label><b class="pos">${p.target_2 ? money(p.target_2) : "—"}</b></div>
        <div><label>VALUE</label><b class="js-value">${money(p.current_price * p.quantity)}</b></div>
      </div>
      <div class="state"><span class="k">CURRENT STATE</span>${esc(p.state)} · qty ${p.quantity}</div>
      <div class="next"><span class="k">NEXT PLANNED ACTION</span>${esc(p.next_action)}</div>
      ${p.intraday ? `<div class="squareoff">AUTO SQUARE-OFF ${esc(p.squareoff_time)} · in <b class="js-squareoff">${mmss(p.seconds_to_squareoff)}</b></div>` : ""}
      <div class="plan">
        <span class="k">EXECUTION PLAN — what happens next, in the order the engine checks it</span>
        ${triggers}
        <div class="trailrule">TRAILING · ${esc(p.trailing_rule || plan.trailing || "—")}</div>
      </div>
      <details>
        <summary>WHY THIS TRADE WAS TAKEN</summary>
        <div class="why">
          <div><span>ENTRY TRIGGER</span><em>${esc(w.entry_trigger)}</em></div>
          <div><span>VOLUME CONFIRM</span><em>${esc(w.volume_confirmation)}</em></div>
          <div><span>TREND CONFIRM</span><em>${esc(w.trend_confirmation)}</em></div>
          <div><span>RISK APPROVAL</span><em>${esc(w.risk_approval)}</em></div>
          <div><span>CAPITAL APPROVAL</span><em>${esc(w.capital_approval)}</em></div>
          <div><span>DUPLICATE CHECK</span><em>${esc(w.duplicate_check)}</em></div>
          <div><span>DAILY LOSS CHECK</span><em>${esc(w.daily_loss_check)}</em></div>
          <div><span>DECISION</span><em>${esc(w.reason_executed)}</em></div>
        </div>
      </details>
    </div>`;
  }).join("");

  // notifications: newly opened trades
  d.positions.forEach(p => {
    if (!seen.positions.has(p.position_id)) {
      seen.positions.add(p.position_id);
      notify(`Trade opened · ${p.symbol}`,
             `${p.strategy} — ${p.direction === "short" ? "sold short" : "bought"} ${p.quantity} @ ${money(p.entry_price)}`,
             "open-" + p.position_id);
    }
  });
}

function renderSignals() {
  const s = state.signals?.data; if (!s) return;
  $("sig-count").textContent = s.potential_count;
  $("rej-count").textContent = s.rejected_count;
  $("sig-waiting").textContent = s.waiting_condition || "";
  const tb = document.querySelector("#t-signals tbody");
  tb.innerHTML = s.potential.length ? s.potential.map(x => `
    <tr><td>${esc(x.time)}</td><td class="sym">${esc(x.symbol)}</td>
    <td>${esc(x.strategy)}</td><td class="r">${money(x.entry)}</td>
    <td class="r">${money(x.stop)}</td><td class="r">${pct(x.confidence)}</td></tr>`).join("")
    : `<tr class="empty"><td colspan="6">No signals yet.</td></tr>`;
  const rb = document.querySelector("#t-rejected tbody");
  rb.innerHTML = s.rejected.length ? s.rejected.map(x => `
    <tr><td>${esc(x.time)}</td><td class="sym">${esc(x.symbol)}</td>
    <td>${esc(x.strategy)}</td><td>${esc(x.reason)}</td></tr>`).join("")
    : `<tr class="empty"><td colspan="4">Nothing rejected yet.</td></tr>`;
}

function renderTimeline() {
  const t = state.timeline?.data; if (!t) return;
  const box = $("timeline");
  box.innerHTML = t.events.length ? t.events.map(e => `
    <div class="row k-${esc(e.kind)}"><time>${esc(e.time)}</time><span>${esc(e.text)}</span></div>`).join("")
    : `<div class="empty">No events yet.</div>`;
}

function renderHealth() {
  const h = state.health?.data; if (!h) return;
  $("h-cpu").textContent = h.cpu_percent === null ? "n/a" : pct(h.cpu_percent);
  $("h-ram").textContent = h.ram_percent === null ? "n/a" : pct(h.ram_percent);
  $("h-cycles").textContent = h.cycles ?? "—";
  const err = $("h-errors");
  err.textContent = h.errors_today ?? 0;
  err.className = "num" + (h.errors_today ? " neg" : "");
  const comp = h.components || {};
  $("h-components").innerHTML = Object.entries(comp).map(([k, v]) => {
    const kl = v === "OK" ? "ok" : (v === "IDLE" ? "" : (v === "HALTED" ? "bad" : "warn"));
    return `<div><label>${esc(k.toUpperCase())}</label><span class="pill ${kl}">${esc(v)}</span></div>`;
  }).join("");
  const trip = $("h-trip");
  if (h.risk_tripped) {
    trip.className = "trip";
    trip.textContent = "RISK TRIPPED: " + (h.trip_reason || "unknown");
  } else { trip.className = "trip hidden"; }
}

function renderClosed() {
  const f = state.performance?.data; if (!f) return;
  const rows = f.closed_trades || [];
  $("ct-count").textContent = rows.length;
  const tb = document.querySelector("#t-closed tbody");
  tb.innerHTML = rows.length ? rows.map(t => `
    <tr><td class="sym">${esc(t.symbol)}</td><td>${esc(t.strategy)}</td>
    <td class="r">${money(t.entry)}</td><td class="r">${money(t.exit)}</td>
    <td class="r">${t.quantity}</td><td>${esc(t.exit_reason)}</td>
    <td class="r ${cls(t.pnl)}">${signed(t.pnl)}</td></tr>`).join("")
    : `<tr class="empty"><td colspan="7">No completed trades today.</td></tr>`;

  rows.forEach(t => {
    const key = `${t.symbol}-${t.exit_ts}`;
    if (!seen.closed.has(key)) {
      seen.closed.add(key);
      const hit = /target/i.test(t.exit_reason) ? "Target hit"
                : (/stop/i.test(t.exit_reason) ? "Stop hit" : "Trade closed");
      notify(`${hit} · ${t.symbol}`,
             `${t.strategy} — exit ${money(t.exit)} · P&L ${signed(t.pnl)}`, key);
    }
  });
}

function renderOrders() {
  const o = state.orders?.data; if (!o) return;
  $("ord-count").textContent = o.count;
  const tb = document.querySelector("#t-orders tbody");
  tb.innerHTML = o.orders.length ? o.orders.map(x => `
    <tr><td>${esc(x.time)}</td><td class="sym">${esc(x.symbol)}</td>
    <td>${esc(x.side)}</td><td>${esc(x.intent)}</td><td class="r">${x.quantity}</td>
    <td class="r">${money(x.avg_fill_price)}</td><td>${esc(x.status)}</td>
    <td>${esc(x.broker_order_id || "—")}</td></tr>`).join("")
    : `<tr class="empty"><td colspan="8">No orders yet.</td></tr>`;
}

function renderLogs() {
  const l = state.logs?.data; if (!l) return;
  let rows = l.logs || [];
  if (logFilter) {
    const q = logFilter.toLowerCase();
    rows = rows.filter(r => (r.message + r.source + r.level + r.time)
      .toLowerCase().includes(q));
  }
  const tb = document.querySelector("#t-logs tbody");
  tb.innerHTML = rows.length ? rows.slice(0, 300).map(r => `
    <tr><td>${esc(r.time)}</td><td class="lvl-${esc(r.level)}">${esc(r.level)}</td>
    <td>${esc(r.source)}</td><td class="msg">${esc(r.message)}</td></tr>`).join("")
    : `<tr class="empty"><td colspan="4">No matching log entries.</td></tr>`;

  (l.logs || []).filter(r => r.level === "ERROR").slice(0, 5).forEach(r => {
    const key = "err-" + r.time + r.message.slice(0, 40);
    notify("System error", r.message.slice(0, 120), key);
  });
}

/* -------------------------------------------------------- notifications */
function notify(title, body, key) {
  if (!notifyOn || firstLoad) return;             // never replay history
  if (seen.notified.has(key)) return;
  seen.notified.add(key);
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  try { new Notification(title, { body, icon: "" }); } catch (e) { /* ignore */ }
}

$("notif-toggle").addEventListener("change", async (e) => {
  notifyOn = e.target.checked;
  if (notifyOn && "Notification" in window && Notification.permission !== "granted") {
    const perm = await Notification.requestPermission();
    if (perm !== "granted") { notifyOn = false; e.target.checked = false; }
  }
});

$("logsearch").addEventListener("input", (e) => {
  logFilter = e.target.value.trim(); renderLogs();
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    refreshSlow();
    refreshFast();
  }
});

/* Two independent cadences: prices and countdowns every second, everything a
 * completed candle changes every few seconds. The countdown keeps ticking
 * locally between polls so it reads smoothly without extra fetches. */
refreshSlow();
refreshFast();
setInterval(refreshSlow, POLL_SLOW_MS);
setInterval(refreshFast, POLL_FAST_MS);
setInterval(() => { if (state.live?.data) renderLive(); }, 1000);
