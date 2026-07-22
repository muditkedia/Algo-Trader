"""Candle parity validation - locally built WS candles vs the Historical API.

FINAL validation experiment before any production market-data redesign:
determines whether 1m/3m/5m candles built locally from the SmartAPI QUOTE
WebSocket are EQUIVALENT to the candles SmartAPI's Historical Candle API
returns for the same exchange time buckets.

Standalone diagnostic - wired into nothing, modifies nothing. It REUSES:

  - production auth + config      SmartApiConfig / SmartApiSession (.env)
  - production instrument cache   SmartApiInstruments via TradingConfig
  - production historical API     SmartApiDataProvider.fetch_ohlcv (public
                                  contract: pacing, backoff, session refresh)
  - the WS collector + local candle builder from websocket_validation.py
    (imported, not copied - the parity collector only ADDS completion
    detection on top of the exact same bucketing)

Method: every time the feed's exchange timestamps roll a (symbol, timeframe)
into a NEW bucket, the previous bucket is complete. After a configurable
finalization delay (default 5s) the utility fetches EXACTLY that one candle
from the Historical API (fromdate == todate window of the bucket) and compares
O/H/L/C/V. Candle boundaries use exchange timestamps ONLY - never local time.
A bucket-label mismatch is recorded as a TIMING mismatch, never as an OHLC
mismatch. The first observed bucket per (symbol, timeframe) is skipped: the
subscription joined it mid-candle, so it is partial by construction.

    .venv/Scripts/python scripts/candle_parity_validation.py                # 25 syms, 15 min
    .venv/Scripts/python scripts/candle_parity_validation.py --symbols 10 --minutes 20
    .venv/Scripts/python scripts/candle_parity_validation.py --timeframes 1m --delay 10

Outputs under user_data/candle_parity/<run-id>/:
    comparison.csv   one row per compared candle (all values + diffs + flags)
    summary.json     machine-readable statistics + verdict
    report.txt       human-readable report + evidence-based verdict
    packets.csv      raw packet log (from the reused collector) - extra evidence

Run during NSE market hours (09:15-15:30 IST). Ctrl-C at any point still
produces all outputs from the comparisons completed so far.
"""

from __future__ import annotations

import argparse
import csv
import json
import queue
import statistics
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import sys                                                    # noqa: E402
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd                                           # noqa: E402

from websocket_validation import (                            # noqa: E402
    Collector, DEFAULT_CACHE_DIR, IST, NSE_CM_EXCHANGE_TYPE, QUOTE_MODE,
    load_symbols, resolve_tokens,
)
from algo.data.providers.smartapi.config import SmartApiConfig      # noqa: E402
from algo.data.providers.smartapi.instruments import SmartApiInstruments  # noqa: E402
from algo.data.providers.smartapi.provider import SmartApiDataProvider    # noqa: E402
from algo.data.providers.smartapi.session import SmartApiSession    # noqa: E402

#: Price equality tolerance: SmartAPI prices are integer paise, so two equal
#: prices differ by < 0.005 after the /100 de-scaling on both sides.
PRICE_TOL = 0.005

COMPARISON_COLUMNS = (
    "symbol", "timeframe", "exchange_bucket_ist",
    "local_open", "hist_open", "local_high", "hist_high",
    "local_low", "hist_low", "local_close", "hist_close",
    "local_volume", "hist_volume",
    "open_match", "high_match", "low_match", "close_match", "volume_match",
    "open_diff", "high_diff", "low_diff", "close_diff", "volume_diff",
    "timing_match", "hist_bucket_seen", "status",
    "local_completed_at_utc", "hist_fetched_at_utc",
    "fetch_lag_after_bucket_end_s", "ticks_in_candle",
)

#: comparison row status values
OK = "compared"
TIMING = "timing_mismatch"
MISSING_HIST = "missing_historical"
API_FAIL = "historical_api_failure"


# ---------------------------------------------------------------- collector

class ParityCollector(Collector):
    """The websocket_validation Collector (identical packet handling, CSV and
    candle building) plus completion detection: when a (symbol, timeframe)
    gets its first packet in a NEW bucket - by exchange timestamp - the
    previous bucket is complete and is queued for historical comparison."""

    def __init__(self, csv_path, symbol_by_token, completions: queue.Queue,
                 timeframes) -> None:
        super().__init__(csv_path, symbol_by_token)
        self.completions = completions
        self.timeframes = tuple(timeframes)      # minutes, e.g. (1, 3, 5)
        self._current = {}                       # (symbol, tf) -> bucket
        self.first_bucket = {}                   # (symbol, tf) -> bucket
        self.skipped_partial = 0

    # Collector._record holds self.lock, so this override is already
    # serialized with snapshot readers.
    def _build_candles(self, symbol, exch_ms, ltp_paise, vol,
                       prev_vol) -> None:
        super()._build_candles(symbol, exch_ms, ltp_paise, vol, prev_vol)
        if not exch_ms or ltp_paise is None:
            return
        for tf in self.timeframes:
            buckets = self.candles.get((symbol, tf))
            if not buckets:
                continue
            latest = max(buckets)
            key = (symbol, tf)
            current = self._current.get(key)
            if current is None:
                self._current[key] = latest
                self.first_bucket[key] = latest
            elif latest > current:
                self._current[key] = latest
                if current == self.first_bucket.get(key):
                    self.skipped_partial += 1     # joined mid-candle: partial
                else:
                    self.completions.put({
                        "symbol": symbol, "tf": tf, "bucket": current,
                        "completed_at": time.time(),
                    })

    def snapshot(self, symbol: str, tf: int, bucket):
        """Thread-safe copy of one finished local candle."""
        with self.lock:
            c = self.candles.get((symbol, tf), {}).get(bucket)
            return list(c) if c else None

    def missing_local_gaps(self) -> dict:
        """(symbol, tf) -> buckets BETWEEN first and last observed bucket that
        never received a packet - i.e. local candles that are simply absent."""
        gaps = {}
        with self.lock:
            for (symbol, tf), buckets in self.candles.items():
                if tf not in self.timeframes or len(buckets) < 2:
                    continue
                have = sorted(buckets)
                expected, cursor = [], have[0]
                step = timedelta(minutes=tf)
                while cursor <= have[-1]:
                    expected.append(cursor)
                    cursor = cursor + step
                absent = [b for b in expected if b not in buckets]
                if absent:
                    gaps[f"{symbol}/{tf}m"] = [b.isoformat() for b in absent]
        return gaps


# ------------------------------------------------------------------ comparer

class Comparer:
    """Consumes completion events, fetches the matching historical candle
    through the production provider, compares, and streams results to CSV.
    Runs on ONE thread so the provider's own pacing serializes API calls."""

    def __init__(self, provider, collector: ParityCollector, csv_path: Path,
                 delay_s: float, retry_wait_s: float = 5.0) -> None:
        self.provider = provider
        self.col = collector
        self.delay_s = delay_s
        self.retry_wait_s = retry_wait_s
        self.lock = threading.Lock()
        self.csv_file = csv_path.open("w", newline="", encoding="utf-8")
        self.csv = csv.writer(self.csv_file)
        self.csv.writerow(COMPARISON_COLUMNS)
        self.rows = 0
        self.results = []          # dict per comparison (in-memory stats)
        self.api_errors = []       # (symbol, tf, bucket, error)
        self.stop = threading.Event()

    # -- historical fetch --------------------------------------------------

    def _fetch_bucket(self, symbol: str, tf: int, bucket) -> tuple:
        """(hist_row_or_None, buckets_seen, error_or_None) for ONE candle.

        The request window is exactly the candle: fromdate == bucket,
        todate == bucket + (tf-1) minutes, so ONLY the completed candle is
        requested - never a large window. One retry for an empty result: the
        API may not have finalized the bar yet.
        """
        start = pd.Timestamp(bucket).tz_convert("UTC")
        end = start + pd.Timedelta(minutes=tf - 1)
        for attempt in (0, 1):
            try:
                frame = self.provider.fetch_ohlcv(symbol, f"{tf}m", start, end)
            except Exception as exc:            # record + continue, never abort
                return None, [], f"{type(exc).__name__}: {exc}"
            if len(frame):
                exact = frame[frame["date"] == start]
                if len(exact):
                    return exact.iloc[0], list(frame["date"]), None
                return None, list(frame["date"]), None
            if attempt == 0 and not self.stop.is_set():
                time.sleep(self.retry_wait_s)   # bar may not be final yet
        return None, [], None

    # -- one completion ----------------------------------------------------

    def process(self, job: dict) -> None:
        symbol, tf, bucket = job["symbol"], job["tf"], job["bucket"]
        wait = job["completed_at"] + self.delay_s - time.time()
        if wait > 0 and not self.stop.is_set():
            time.sleep(wait)

        local = self.col.snapshot(symbol, tf, bucket)
        if local is None:                       # cannot happen in practice
            return
        lo, lh, ll, lc, vol_open, vol_close, ticks = local
        lvol = (vol_close - vol_open
                if None not in (vol_open, vol_close) else None)

        hist_row, seen, error = self._fetch_bucket(symbol, tf, bucket)
        fetched_at = time.time()
        bucket_end = (bucket + timedelta(minutes=tf)).timestamp()

        rec = {
            "symbol": symbol, "tf": tf, "bucket": bucket,
            "local": (lo, lh, ll, lc, lvol), "ticks": ticks,
            "completed_at": job["completed_at"], "fetched_at": fetched_at,
            "fetch_lag_s": fetched_at - bucket_end,
        }
        if error is not None:
            rec["status"] = API_FAIL
            self.api_errors.append((symbol, f"{tf}m", bucket.isoformat(),
                                    error))
        elif hist_row is None and not seen:
            rec["status"] = MISSING_HIST
        elif hist_row is None:
            rec["status"] = TIMING
            rec["hist_buckets_seen"] = [str(t) for t in seen]
        else:
            ho, hh, hl, hc = (float(hist_row["open"]), float(hist_row["high"]),
                              float(hist_row["low"]), float(hist_row["close"]))
            hvol = float(hist_row["volume"])
            rec["status"] = OK
            rec["hist"] = (ho, hh, hl, hc, hvol)
            rec["matches"] = {
                "open": abs(lo - ho) < PRICE_TOL,
                "high": abs(lh - hh) < PRICE_TOL,
                "low": abs(ll - hl) < PRICE_TOL,
                "close": abs(lc - hc) < PRICE_TOL,
                "volume": lvol is not None and abs(lvol - hvol) < 0.5,
            }
            rec["diffs"] = {
                "open": round(lo - ho, 4), "high": round(lh - hh, 4),
                "low": round(ll - hl, 4), "close": round(lc - hc, 4),
                "volume": (lvol - hvol) if lvol is not None else None,
            }
        self._write(rec)

    def _write(self, rec: dict) -> None:
        m = rec.get("matches", {})
        d = rec.get("diffs", {})
        hist = rec.get("hist", (None,) * 5)
        lo, lh, ll, lc, lvol = rec["local"]

        def utc(epoch):
            return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()

        row = [
            rec["symbol"], f"{rec['tf']}m", rec["bucket"].isoformat(),
            lo, hist[0], lh, hist[1], ll, hist[2], lc, hist[3], lvol, hist[4],
            m.get("open"), m.get("high"), m.get("low"), m.get("close"),
            m.get("volume"),
            d.get("open"), d.get("high"), d.get("low"), d.get("close"),
            d.get("volume"),
            rec["status"] != TIMING if rec["status"] in (OK, TIMING) else None,
            ";".join(rec.get("hist_buckets_seen", [])),
            rec["status"], utc(rec["completed_at"]), utc(rec["fetched_at"]),
            round(rec["fetch_lag_s"], 2), rec["ticks"],
        ]
        with self.lock:
            self.results.append(rec)
            self.csv.writerow(row)
            self.csv_file.flush()
            self.rows += 1

    def close(self) -> None:
        with self.lock:
            self.csv_file.close()


def comparison_worker(comparer: Comparer, completions: queue.Queue) -> None:
    while True:
        try:
            job = completions.get(timeout=0.5)
        except queue.Empty:
            if comparer.stop.is_set():
                return
            continue
        if job is None:
            return
        try:
            comparer.process(job)
        except Exception as exc:                # never die mid-run
            comparer.api_errors.append((job.get("symbol"),
                                        f"{job.get('tf')}m",
                                        str(job.get("bucket")),
                                        f"worker error: {exc}"))


# ---------------------------------------------------------------- summarize

def _pct(num, den):
    return round(100.0 * num / den, 4) if den else None


def summarize(comparer: Comparer, col: ParityCollector, run_meta: dict) -> dict:
    with comparer.lock:                  # a late worker append cannot race us
        res = list(comparer.results)
    ok = [r for r in res if r["status"] == OK]
    timing = [r for r in res if r["status"] == TIMING]
    missing_hist = [r for r in res if r["status"] == MISSING_HIST]
    failures = [r for r in res if r["status"] == API_FAIL]

    field_match = {f: sum(1 for r in ok if r["matches"][f])
                   for f in ("open", "high", "low", "close", "volume")}
    all4 = sum(1 for r in ok if all(r["matches"][f]
                                    for f in ("open", "high", "low", "close")))

    ohlc_diffs = [abs(r["diffs"][f]) for r in ok
                  for f in ("open", "high", "low", "close")]
    vol_diffs = [abs(r["diffs"]["volume"]) for r in ok
                 if r["diffs"]["volume"] is not None]

    per_symbol_bad = defaultdict(int)
    for r in ok:
        if not all(r["matches"][f] for f in ("open", "high", "low", "close",
                                             "volume")):
            per_symbol_bad[r["symbol"]] += 1
    worst_symbol = max(per_symbol_bad, key=per_symbol_bad.get) \
        if per_symbol_bad else None

    worst_candle = None
    if ok:
        w = max(ok, key=lambda r: max(abs(r["diffs"][f])
                                      for f in ("open", "high", "low",
                                                "close")))
        worst_diff = max(abs(w["diffs"][f])
                         for f in ("open", "high", "low", "close"))
        if worst_diff > 0:
            worst_candle = {"symbol": w["symbol"], "timeframe": f"{w['tf']}m",
                            "bucket": w["bucket"].isoformat(),
                            "max_abs_ohlc_diff": worst_diff}

    lags = [r["fetch_lag_s"] for r in res]
    gaps = col.missing_local_gaps()

    return {
        "run": run_meta,
        "comparisons": {
            "total_completions": len(res),
            "compared": len(ok),
            "timing_mismatches": len(timing),
            "missing_historical_candles": len(missing_hist),
            "historical_api_failures": len(failures),
            "skipped_partial_first_buckets": col.skipped_partial,
        },
        "parity": {
            "open_match_pct": _pct(field_match["open"], len(ok)),
            "high_match_pct": _pct(field_match["high"], len(ok)),
            "low_match_pct": _pct(field_match["low"], len(ok)),
            "close_match_pct": _pct(field_match["close"], len(ok)),
            "ohlc_exact_match_pct": _pct(all4, len(ok)),
            "volume_exact_match_pct": _pct(field_match["volume"], len(ok)),
            "avg_abs_ohlc_diff": round(statistics.fmean(ohlc_diffs), 6)
            if ohlc_diffs else None,
            "max_abs_ohlc_diff": round(max(ohlc_diffs), 4)
            if ohlc_diffs else None,
            "avg_abs_volume_diff": round(statistics.fmean(vol_diffs), 2)
            if vol_diffs else None,
            "max_abs_volume_diff": max(vol_diffs) if vol_diffs else None,
            "worst_symbol": worst_symbol,
            "worst_symbol_mismatches": per_symbol_bad.get(worst_symbol),
            "worst_candle": worst_candle,
        },
        "missing_local_candles": {
            "count": sum(len(v) for v in gaps.values()),
            "detail": gaps,
        },
        "timing": {
            "note": "fetch lag is measured from the exchange bucket END to "
                    "the historical fetch; a locally built candle is already "
                    "final at the bucket end, so the lag is the availability "
                    "lead of the local candle over historical polling",
            "avg_fetch_lag_s": round(statistics.fmean(lags), 2)
            if lags else None,
            "median_fetch_lag_s": round(statistics.median(lags), 2)
            if lags else None,
            "max_fetch_lag_s": round(max(lags), 2) if lags else None,
        },
        "api_errors": [
            {"symbol": s, "timeframe": tf, "bucket": b, "error": e}
            for s, tf, b, e in comparer.api_errors[:50]],
    }


def verdict(s: dict) -> tuple:
    """PASS / FAIL / INSUFFICIENT DATA, from measured evidence only."""
    c, p = s["comparisons"], s["parity"]
    if c["compared"] == 0:
        if c["total_completions"] == 0:
            return "INSUFFICIENT DATA", [
                "no local candles completed - run during NSE market hours "
                "(09:15-15:30 IST) and long enough for candles to complete"]
        return "FAIL", [
            f"{c['total_completions']} candles completed locally but NONE "
            f"could be verified ({c['missing_historical_candles']} missing "
            f"historical, {c['timing_mismatches']} timing mismatches, "
            f"{c['historical_api_failures']} API failures)"]

    reasons, ok = [], True

    ohlc = p["ohlc_exact_match_pct"]
    if ohlc is None or ohlc < 99.99:
        ok = False
        reasons.append(f"OHLC parity {ohlc}% < 99.99% required "
                       f"(max abs diff {p['max_abs_ohlc_diff']})")
    else:
        reasons.append(f"OHLC parity {ohlc}% (>= 99.99%)")

    if c["timing_mismatches"] > 0:
        ok = False
        reasons.append(f"{c['timing_mismatches']} timing mismatch(es): "
                       "historical buckets did not align with local buckets "
                       "- systematic timing drift")
    else:
        reasons.append("0 timing mismatches - bucket labels align exactly")

    missing = (c["missing_historical_candles"]
               + s["missing_local_candles"]["count"])
    if missing > 0:
        ok = False
        reasons.append(f"missing candles: {c['missing_historical_candles']} "
                       f"historical, {s['missing_local_candles']['count']} "
                       "local")
    else:
        reasons.append("no missing candles on either side")

    vol = p["volume_exact_match_pct"]
    if vol is None or vol < 99.0:
        ok = False
        reasons.append(f"volume parity {vol}% - systematic volume problem "
                       f"(avg abs diff {p['avg_abs_volume_diff']}, "
                       f"max {p['max_abs_volume_diff']})")
    else:
        reasons.append(f"volume parity {vol}%")

    if c["historical_api_failures"]:
        reasons.append(f"note: {c['historical_api_failures']} historical API "
                       "failure(s) - those candles are unverified")
    if c["compared"] < 30:
        reasons.append(f"note: only {c['compared']} candles compared - "
                       "small sample; re-run longer for confidence")

    return ("PASS" if ok else "FAIL"), reasons


def write_report(path: Path, s: dict, v: str, reasons: list) -> None:
    lines = ["Candle parity validation - local WS candles vs Historical API",
             "=" * 66, ""]

    def section(title, obj):
        lines.append(title)
        lines.append("-" * len(title))
        for k, val in obj.items():
            if k != "detail":
                lines.append(f"  {k}: {val}")
        lines.append("")

    section("Run", s["run"])
    section("Comparisons", s["comparisons"])
    section("Parity", s["parity"])
    section("Missing local candles", s["missing_local_candles"])
    section("Timing", s["timing"])
    if s["api_errors"]:
        lines.append("Historical API errors (first 50)")
        lines.append("-" * 32)
        for e in s["api_errors"]:
            lines.append(f"  {e['symbol']} {e['timeframe']} {e['bucket']}: "
                         f"{e['error']}")
        lines.append("")

    lines.append("VERDICT (measured evidence only)")
    lines.append("-" * 32)
    lines.append(f"  {v}")
    for r in reasons:
        lines.append(f"  - {r}")
    lines.append("")
    if v == "PASS":
        lines.append("  Engineering recommendation: locally built QUOTE-feed "
                     "candles match the Historical API for the measured "
                     "sample. Routine per-bar historical polling can be "
                     "replaced by local candle construction, with the "
                     "Historical API retained for startup, reconnect "
                     "recovery, repair and periodic spot-validation.")
    elif v == "FAIL":
        lines.append("  Engineering recommendation: do NOT replace "
                     "historical polling yet - the mismatches above must be "
                     "understood (and either fixed or shown immaterial) "
                     "first.")
    path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


# --------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare locally built QUOTE-feed candles against the "
                    "Historical Candle API (standalone evidence gathering; "
                    "touches no production code)")
    parser.add_argument("--symbols", type=int, default=25,
                        help="subscription size, 1-300 (default 25; the "
                             "historical API sustains ~1 fetch/s, so large "
                             "sizes back-log the comparison queue)")
    parser.add_argument("--symbols-file", default="user_data/_top300.txt")
    parser.add_argument("--minutes", type=float, default=15.0,
                        help="observation runtime (default 15)")
    parser.add_argument("--delay", type=float, default=5.0,
                        help="wait after candle completion before fetching "
                             "the historical candle (default 5s)")
    parser.add_argument("--timeframes", default="1m,3m,5m",
                        help="comma list from {1m,3m,5m} (default all)")
    parser.add_argument("--out", default="user_data/candle_parity")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    parser.add_argument("--drain-seconds", type=float, default=180.0,
                        help="max time after the feed stops to finish "
                             "fetching queued comparisons (default 180)")
    args = parser.parse_args()

    if not 1 <= args.symbols <= 300:
        parser.error("--symbols must be between 1 and 300")
    try:
        timeframes = tuple(sorted(
            int(tf.strip().rstrip("m")) for tf in args.timeframes.split(",")))
    except ValueError:
        parser.error(f"bad --timeframes {args.timeframes!r}")
    if any(tf not in (1, 3, 5) for tf in timeframes):
        parser.error("--timeframes supports only 1m, 3m, 5m")

    completions_per_min = args.symbols * sum(1.0 / tf for tf in timeframes)
    if completions_per_min > 55:
        print(f"WARNING: ~{completions_per_min:.0f} candle completions/min "
              "exceeds the ~55 fetches/min the historical API sustains at "
              "1 req/s - the queue will back-log; prefer fewer symbols")

    print("Starting...")
    run_id = datetime.now(tz=IST).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Loading config...")
    config = SmartApiConfig.from_env()
    print("Config loaded.")

    print("Loading instrument master...")
    instruments = SmartApiInstruments(config.instruments_url,
                                      cache_dir=Path(args.cache_dir))
    print("Instrument master loaded.")

    print("Resolving symbols...")
    symbols = load_symbols(Path(args.symbols_file), args.symbols)
    token_by_symbol, unresolved = resolve_tokens(instruments, symbols)
    symbol_by_token = {t: s for s, t in token_by_symbol.items()}
    if unresolved:
        print(f"unresolved symbols (skipped): {unresolved}")
    if not token_by_symbol:
        print("no symbols resolved to tokens - aborting")
        return 2
    print(f"Symbols resolved ({len(token_by_symbol)}).")

    print("Logging into SmartAPI...")
    try:
        session = SmartApiSession(config).ensure()
    except Exception as exc:
        print(f"SmartAPI login failed: {exc}")
        return 2
    jwt = getattr(session.client, "access_token", None)
    if not jwt or not session.feed_token:
        print("login succeeded but jwt/feed token missing - aborting")
        return 2
    print("Login successful.")

    provider = SmartApiDataProvider(session, instruments)
    completions: queue.Queue = queue.Queue()
    col = ParityCollector(out_dir / "packets.csv", symbol_by_token,
                          completions, timeframes)
    comparer = Comparer(provider, col, out_dir / "comparison.csv", args.delay)

    from SmartApi.smartWebSocketV2 import SmartWebSocketV2

    conn = {"opens": 0, "closes": 0, "errors": []}
    tokens = sorted(symbol_by_token)
    sub_request = [{"exchangeType": NSE_CM_EXCHANGE_TYPE, "tokens": tokens}]

    print("Creating WebSocket...")
    sws = SmartWebSocketV2(jwt, config.api_key, config.client_code,
                           session.feed_token, max_retry_attempt=3)

    def on_open(wsapp):
        conn["opens"] += 1
        sws.subscribe(f"parity-{run_id}", QUOTE_MODE, sub_request)
        print(f"[{datetime.now(tz=IST):%H:%M:%S}] connected "
              f"(open #{conn['opens']}), subscribed {len(tokens)} tokens")

    def on_data(wsapp, message):
        if isinstance(message, dict):
            col.on_packet(message)

    def on_error(wsapp, error):
        conn["errors"].append(str(error))
        print(f"[ws error] {error}")

    def on_close(wsapp, *close_args):
        conn["closes"] += 1
        print(f"[{datetime.now(tz=IST):%H:%M:%S}] websocket closed "
              f"(#{conn['closes']})")

    sws.on_open = on_open
    sws.on_data = on_data
    sws.on_error = on_error
    sws.on_close = on_close

    worker = threading.Thread(target=comparison_worker,
                              args=(comparer, completions),
                              daemon=True, name="parity-comparer")
    worker.start()

    print("Starting WebSocket thread...")
    ws_thread = threading.Thread(target=sws.connect, daemon=True,
                                 name="smartapi-ws")
    ws_thread.start()
    print("Waiting for packets...")

    runtime_s = args.minutes * 60.0
    started = time.time()
    interrupted = False
    try:
        while time.time() - started < runtime_s:
            time.sleep(1.0)
            col.flush()
            elapsed = int(time.time() - started)
            if elapsed and elapsed % 30 == 0:
                print(f"  t+{elapsed:>4}s packets={col.total_packets} "
                      f"compared={comparer.rows} queued={completions.qsize()}")
    except KeyboardInterrupt:
        interrupted = True
        print("interrupted - draining queued comparisons "
              "(Ctrl-C again to stop immediately)")
    finally:
        try:
            sws.close_connection()
        except Exception as exc:
            print(f"close_connection: {exc}")

        # Completed comparisons must not be lost: drain the queue (bounded).
        drain_until = time.time() + (args.drain_seconds if not interrupted
                                     else min(args.drain_seconds, 60.0))
        try:
            while not completions.empty() and time.time() < drain_until:
                time.sleep(1.0)
                print(f"  draining... queued={completions.qsize()} "
                      f"compared={comparer.rows}")
        except KeyboardInterrupt:
            print("stopping drain")
        comparer.stop.set()
        worker.join(timeout=15)
        ws_thread.join(timeout=10)
        col.close()
        comparer.close()

        run_meta = {
            "run_id": run_id,
            "runtime_seconds": round(time.time() - started, 1),
            "symbols_subscribed": len(token_by_symbol),
            "symbols_unresolved": unresolved,
            "timeframes": [f"{tf}m" for tf in timeframes],
            "finalization_delay_s": args.delay,
            "ws_connects": conn["opens"],
            "ws_reconnects": max(0, conn["opens"] - 1),
            "ws_errors": conn["errors"][:10],
            "interrupted": interrupted,
        }
        s = summarize(comparer, col, run_meta)
        v, reasons = verdict(s)
        s["verdict"] = {"result": v, "evidence": reasons}
        (out_dir / "summary.json").write_text(
            json.dumps(s, indent=2, default=str), encoding="utf-8")
        write_report(out_dir / "report.txt", s, v, reasons)
        print(f"\nartifacts: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
