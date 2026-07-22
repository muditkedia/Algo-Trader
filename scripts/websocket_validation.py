"""SmartAPI WebSocket QUOTE-mode validation - standalone diagnostic utility.

Engineering experiment (NOT production code, wired into nothing): measure
whether SmartAPI's QUOTE WebSocket feed is reliable, complete and scalable
enough to replace routine historical-candle downloads with locally built
candles, keeping the historical API only for startup / recovery / repair.

    .venv/Scripts/python scripts/websocket_validation.py                 # 50 syms, 5 min
    .venv/Scripts/python scripts/websocket_validation.py --symbols 300 --minutes 5
    .venv/Scripts/python scripts/websocket_validation.py --symbols-file nifty100.txt

Reuses production authentication (SmartApiConfig/.env -> SmartApiSession) and
the authoritative instrument master (SmartApiInstruments, D-038) - nothing is
duplicated and NO production module is modified. The session is NOT terminated
on exit so a concurrently running paper/live engine is never logged out.

Outputs (under --out, default user_data/ws_validation/<run-id>/):
    packets.csv    every packet received, nothing discarded
    candles.csv    1m/3m/5m OHLCV candles built locally from the feed
    summary.json   machine-readable statistics
    report.txt     human-readable report + evidence-based conclusion
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import sys                                                    # noqa: E402
sys.path.insert(0, str(ROOT / "src"))

from algo.data.providers.smartapi.config import SmartApiConfig      # noqa: E402
from algo.data.providers.smartapi.instruments import SmartApiInstruments  # noqa: E402
from algo.data.providers.smartapi.session import SmartApiSession    # noqa: E402
from algo.trading.config import TradingConfig                       # noqa: E402

#: The production instrument-master cache. Derived from TradingConfig (the
#: same source scripts/run_trading.py uses: <store_dir>/_instruments), so the
#: validator never guesses a path the production system doesn't use.
DEFAULT_CACHE_DIR = str(Path(TradingConfig.store_dir) / "_instruments")

IST = timezone(timedelta(hours=5, minutes=30))

#: Fields the SDK documents for QUOTE mode (mode 2). Prices arrive as integer
#: paise (price * 100); volume/quantity fields are integers.
QUOTE_PRICE_FIELDS = (
    "last_traded_price", "average_traded_price", "open_price_of_the_day",
    "high_price_of_the_day", "low_price_of_the_day", "closed_price",
)
QUOTE_EXPECTED_FIELDS = (
    "subscription_mode", "exchange_type", "token", "sequence_number",
    "exchange_timestamp", "last_traded_price", "last_traded_quantity",
    "average_traded_price", "volume_trade_for_the_day",
    "total_buy_quantity", "total_sell_quantity",
) + QUOTE_PRICE_FIELDS[2:]

CSV_COLUMNS = (
    "local_ts_utc", "local_epoch_ms", "exchange_timestamp_ms",
    "exchange_ts_ist", "symbol", "token", "ltp", "volume_traded_today",
    "last_traded_quantity", "average_traded_price", "day_open", "day_high",
    "day_low", "prev_close", "total_buy_quantity", "total_sell_quantity",
    "sequence_number", "subscription_mode", "exchange_type", "extra_json",
)

CANDLE_MINUTES = (1, 3, 5)

NSE_CM_EXCHANGE_TYPE = 1
QUOTE_MODE = 2


# --------------------------------------------------------------------- inputs

def load_symbols(path: Path, count: int) -> list:
    """First ``count`` symbols from a production universe file (# = comment)."""
    symbols = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            symbols.append(line.upper())
    if len(symbols) < count:
        print(f"WARNING: {path} has only {len(symbols)} symbols "
              f"(requested {count}); using all of them")
    return symbols[:count]


def resolve_tokens(instruments: SmartApiInstruments, symbols: list):
    """symbol -> token via the authoritative master; report what didn't map."""
    token_by_symbol, unresolved = {}, []
    for sym in symbols:
        token = instruments.token_for(sym)
        if token:
            token_by_symbol[sym] = str(token)
        else:
            unresolved.append(sym)
    return token_by_symbol, unresolved


# ------------------------------------------------------------------ collector

class Collector:
    """Receives every packet, streams it to CSV, accumulates statistics and
    builds 1m/3m/5m candles locally. Thread-safe: the SDK invokes callbacks
    from its own websocket thread."""

    def __init__(self, csv_path: Path, symbol_by_token: dict):
        self.lock = threading.Lock()
        self.symbol_by_token = symbol_by_token
        self.csv_file = csv_path.open("w", newline="", encoding="utf-8")
        self.csv = csv.writer(self.csv_file)
        self.csv.writerow(CSV_COLUMNS)

        self.total_packets = 0
        self.per_symbol = defaultdict(int)
        self.last_local = {}                 # token -> local epoch s
        self.intervals = defaultdict(list)   # token -> [s]
        self.last_exchange_ts = {}           # token -> ms
        self.out_of_order = 0
        self.duplicates = 0
        self.seen_keys = set()               # (token, exch_ts, seq, ltp, vol)
        self.latencies_ms = []
        self.last_volume = {}                # token -> cumulative volume
        self.volume_violations = 0
        self.missing_fields = defaultdict(int)
        self.seq_regressions = 0
        self.last_seq = {}
        self.unknown_tokens = defaultdict(int)
        self.first_packet_at = None
        self.last_packet_at = None
        # candles: (symbol, tf_minutes) -> {bucket_ist: [o, h, l, c,
        #                                   vol_open_cum, vol_close_cum, n]}
        self.candles = defaultdict(dict)

    # -- per packet --------------------------------------------------------

    def on_packet(self, message: dict) -> None:
        now = time.time()
        with self.lock:
            self._record(message, now)

    def _record(self, m: dict, now: float) -> None:
        self.total_packets += 1
        self.first_packet_at = self.first_packet_at or now
        self.last_packet_at = now

        token = str(m.get("token") or "")
        symbol = self.symbol_by_token.get(token)
        if symbol is None:
            self.unknown_tokens[token] += 1
            symbol = f"?{token}"
        self.per_symbol[symbol] += 1

        for f in QUOTE_EXPECTED_FIELDS:
            if m.get(f) is None:
                self.missing_fields[f] += 1

        exch_ms = m.get("exchange_timestamp") or 0
        seq = m.get("sequence_number")
        ltp_paise = m.get("last_traded_price")
        vol = m.get("volume_trade_for_the_day")

        key = (token, exch_ms, seq, ltp_paise, vol)
        if key in self.seen_keys:
            self.duplicates += 1
        self.seen_keys.add(key)

        prev_ms = self.last_exchange_ts.get(token)
        if prev_ms is not None and exch_ms and exch_ms < prev_ms:
            self.out_of_order += 1
        if exch_ms:
            self.last_exchange_ts[token] = exch_ms
            self.latencies_ms.append(now * 1000.0 - exch_ms)

        if seq is not None:
            if token in self.last_seq and seq < self.last_seq[token]:
                self.seq_regressions += 1
            self.last_seq[token] = seq

        prev_local = self.last_local.get(token)
        if prev_local is not None:
            self.intervals[token].append(now - prev_local)
        self.last_local[token] = now

        prev_vol = self.last_volume.get(token)
        if prev_vol is not None and vol is not None and vol < prev_vol:
            self.volume_violations += 1
        if vol is not None:
            self.last_volume[token] = vol

        self._build_candles(symbol, exch_ms, ltp_paise, vol, prev_vol)
        self._write_row(m, now, symbol, token, exch_ms)

    def _build_candles(self, symbol, exch_ms, ltp_paise, vol,
                       prev_vol) -> None:
        """prev_vol is the cumulative volume BEFORE this packet: it is the
        candle-open baseline, so the candle's volume delta includes this
        packet's own traded quantity. None on a symbol's first packet - that
        first candle is flagged ``volume_baseline_known=False`` in the CSV."""
        if not exch_ms or ltp_paise is None:
            return
        price = ltp_paise / 100.0
        ts = datetime.fromtimestamp(exch_ms / 1000.0, tz=IST)
        for tf in CANDLE_MINUTES:
            bucket = ts.replace(second=0, microsecond=0,
                                minute=ts.minute - ts.minute % tf)
            c = self.candles[(symbol, tf)].get(bucket)
            if c is None:
                self.candles[(symbol, tf)][bucket] = [
                    price, price, price, price, prev_vol, vol, 1]
            else:
                c[1] = max(c[1], price)
                c[2] = min(c[2], price)
                c[3] = price
                if vol is not None:
                    c[5] = vol
                c[6] += 1

    def _write_row(self, m, now, symbol, token, exch_ms) -> None:
        def price(name):
            v = m.get(name)
            return v / 100.0 if isinstance(v, (int, float)) else v

        known = set(CSV_COLUMNS) | set(QUOTE_EXPECTED_FIELDS) | {
            "subscription_mode_val"}
        extra = {k: v for k, v in m.items() if k not in known}
        self.csv.writerow([
            datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            int(now * 1000), exch_ms,
            (datetime.fromtimestamp(exch_ms / 1000.0, tz=IST).isoformat()
             if exch_ms else ""),
            symbol, token, price("last_traded_price"),
            m.get("volume_trade_for_the_day"), m.get("last_traded_quantity"),
            price("average_traded_price"), price("open_price_of_the_day"),
            price("high_price_of_the_day"), price("low_price_of_the_day"),
            price("closed_price"), m.get("total_buy_quantity"),
            m.get("total_sell_quantity"), m.get("sequence_number"),
            m.get("subscription_mode"), m.get("exchange_type"),
            json.dumps(extra, default=str) if extra else "",
        ])

    def flush(self) -> None:
        with self.lock:
            self.csv_file.flush()

    def close(self) -> None:
        with self.lock:
            self.csv_file.close()


# ----------------------------------------------------------------- statistics

def percentile(values: list, pct: float):
    if not values:
        return None
    values = sorted(values)
    idx = min(len(values) - 1, int(round(pct / 100.0 * (len(values) - 1))))
    return values[idx]


def summarize(col: Collector, subscribed: list, unresolved: list,
              runtime_s: float, conn) -> dict:
    all_intervals = [d for ds in col.intervals.values() for d in ds]
    counts = [col.per_symbol.get(s, 0) for s in subscribed]
    nonzero = [c for c in counts if c > 0]
    median_count = statistics.median(nonzero) if nonzero else 0
    silent = [s for s in subscribed if col.per_symbol.get(s, 0) == 0]
    sparse = [s for s in subscribed
              if 0 < col.per_symbol.get(s, 0) < 0.25 * median_count]

    max_gap = None
    max_gap_symbol = None
    for token, ds in col.intervals.items():
        if ds and (max_gap is None or max(ds) > max_gap):
            max_gap = max(ds)
            max_gap_symbol = col.symbol_by_token.get(token, token)

    minutes = max(runtime_s / 60.0, 1e-9)
    # minute coverage: fraction of (symbol, whole-minute) cells during the run
    # that received at least one packet - the direct feasibility test for
    # building 1m candles locally.
    per_symbol_minutes = defaultdict(set)
    for (symbol, tf), buckets in col.candles.items():
        if tf == 1:
            per_symbol_minutes[symbol] = set(buckets)
    whole_minutes = int(runtime_s // 60)
    covered = sum(min(len(v), whole_minutes)
                  for s, v in per_symbol_minutes.items() if s in subscribed)
    coverage_cells = whole_minutes * len(subscribed)
    minute_coverage = covered / coverage_cells if coverage_cells else None

    candle_counts = {f"{tf}m": sum(len(b) for (s, t), b in col.candles.items()
                                   if t == tf) for tf in CANDLE_MINUTES}

    return {
        "run": {
            "started_utc": conn["started_utc"],
            "runtime_seconds": round(runtime_s, 1),
            "symbols_requested": conn["requested"],
            "symbols_subscribed": len(subscribed),
            "symbols_unresolved": unresolved,
            "mode": "QUOTE (2)",
        },
        "packets": {
            "total": col.total_packets,
            "per_minute_avg": round(col.total_packets / minutes, 1),
            "per_symbol_min": min(counts) if counts else 0,
            "per_symbol_median": median_count,
            "per_symbol_max": max(counts) if counts else 0,
            "unknown_token_packets": sum(col.unknown_tokens.values()),
        },
        "update_intervals_seconds": {
            "average": round(statistics.fmean(all_intervals), 3)
            if all_intervals else None,
            "median": round(statistics.median(all_intervals), 3)
            if all_intervals else None,
            "p95": round(percentile(all_intervals, 95), 3)
            if all_intervals else None,
            "max_gap": round(max_gap, 3) if max_gap is not None else None,
            "max_gap_symbol": max_gap_symbol,
        },
        "quality": {
            "symbols_silent": silent,
            "symbols_sparse_lt_25pct_of_median": sparse,
            "duplicate_packets": col.duplicates,
            "out_of_order_exchange_timestamps": col.out_of_order,
            "sequence_regressions": col.seq_regressions,
            "volume_monotonicity_violations": col.volume_violations,
            "missing_field_counts": dict(col.missing_fields),
        },
        "latency_ms_local_minus_exchange": {
            "note": "includes local clock skew; treat relatively",
            "average": round(statistics.fmean(col.latencies_ms), 1)
            if col.latencies_ms else None,
            "median": round(statistics.median(col.latencies_ms), 1)
            if col.latencies_ms else None,
            "p95": round(percentile(col.latencies_ms, 95), 1)
            if col.latencies_ms else None,
        },
        "connection": {
            "connects": conn["opens"],
            "reconnects": max(0, conn["opens"] - 1),
            "disconnects": conn["closes"],
            "errors": conn["errors"],
        },
        "candles_built": candle_counts,
        "completeness": {
            "symbols_with_data_pct": round(100.0 * len(nonzero)
                                           / len(subscribed), 2)
            if subscribed else None,
            "minute_coverage_pct": round(100.0 * minute_coverage, 2)
            if minute_coverage is not None else None,
            "whole_minutes_observed": whole_minutes,
        },
    }


def conclude(s: dict) -> tuple:
    """Evidence-based verdict: FEASIBLE / NOT FEASIBLE / INCONCLUSIVE."""
    total = s["packets"]["total"]
    if total < 100 or s["completeness"]["whole_minutes_observed"] < 1:
        return "INCONCLUSIVE", [
            f"only {total} packets in "
            f"{s['run']['runtime_seconds']}s - too little evidence "
            "(market closed, or feed not delivering). Re-run during NSE "
            "market hours (09:15-15:30 IST)."]

    reasons = []
    ok = True

    cov = s["completeness"]["symbols_with_data_pct"] or 0
    if cov < 99.0:
        ok = False
        reasons.append(f"only {cov}% of subscribed symbols received any "
                       f"packet (silent: {len(s['quality']['symbols_silent'])})")
    else:
        reasons.append(f"symbol coverage {cov}%")

    mc = s["completeness"]["minute_coverage_pct"]
    if mc is not None:
        if mc < 99.0:
            ok = False
            reasons.append(f"minute coverage {mc}% - locally built 1m candles "
                           "would have empty cells needing API repair")
        else:
            reasons.append(f"minute coverage {mc}% - every symbol ticked in "
                           "effectively every minute")

    gap = s["update_intervals_seconds"]["max_gap"]
    if gap is not None:
        if gap >= 60.0:
            ok = False
            reasons.append(f"max update gap {gap}s spans a whole 1m candle")
        else:
            reasons.append(f"max update gap {gap}s (< 60s candle width)")

    vv = s["quality"]["volume_monotonicity_violations"]
    if vv > max(1, total // 10000):
        ok = False
        reasons.append(f"{vv} cumulative-volume regressions - candle volume "
                       "deltas would be unreliable")
    else:
        reasons.append(f"volume monotonic ({vv} violations in {total} packets)")

    rc = s["connection"]["reconnects"]
    if rc > 0:
        reasons.append(f"{rc} reconnect(s) observed - gap-repair via the "
                       "historical API remains mandatory")

    miss = {k: v for k, v in s["quality"]["missing_field_counts"].items()
            if v > 0}
    if miss:
        reasons.append(f"missing fields observed: {miss}")

    verdict = "FEASIBLE" if ok else "NOT FEASIBLE"
    return verdict, reasons


def write_report(path: Path, s: dict, verdict: str, reasons: list) -> None:
    lines = ["SmartAPI QUOTE WebSocket validation report",
             "=" * 60, ""]

    def section(title, obj):
        lines.append(title)
        lines.append("-" * len(title))
        for k, v in obj.items():
            lines.append(f"  {k}: {v}")
        lines.append("")

    section("Run", s["run"])
    section("Packets", s["packets"])
    section("Update intervals (s)", s["update_intervals_seconds"])
    section("Quality", {k: v for k, v in s["quality"].items()})
    section("Latency (ms, local clock minus exchange ts)",
            s["latency_ms_local_minus_exchange"])
    section("Connection", s["connection"])
    section("Locally built candles", s["candles_built"])
    section("Completeness", s["completeness"])

    lines.append("CONCLUSION (measured evidence only)")
    lines.append("-" * 34)
    lines.append(f"  Verdict: {verdict}")
    for r in reasons:
        lines.append(f"  - {r}")
    if verdict == "FEASIBLE":
        lines.append("")
        lines.append("  QUOTE mode appears sufficient to build 1m/3m/5m "
                     "candles locally during normal operation, with the "
                     "historical API retained for startup, reconnect "
                     "recovery, repair and validation.")
    path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


# ----------------------------------------------------------------------- main

def write_candles_csv(path: Path, col: Collector) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "timeframe", "bucket_ist", "open", "high",
                    "low", "close", "volume_delta", "cum_volume_close",
                    "ticks", "volume_baseline_known"])
        for (symbol, tf), buckets in sorted(col.candles.items()):
            for bucket in sorted(buckets):
                o, h, l, c, vol_open, vol_close, n = buckets[bucket]
                delta = (vol_close - vol_open
                         if None not in (vol_open, vol_close) else None)
                w.writerow([symbol, f"{tf}m", bucket.isoformat(), o, h, l, c,
                            delta, vol_close, n,
                            vol_open is not None])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SmartAPI QUOTE WebSocket feasibility measurement "
                    "(standalone; touches no production code)")
    parser.add_argument("--symbols", type=int, default=50,
                        choices=[50, 100, 200, 300],
                        help="subscription size (default 50)")
    parser.add_argument("--symbols-file", default="user_data/_top300.txt",
                        help="production universe file (default _top300.txt)")
    parser.add_argument("--minutes", type=float, default=5.0,
                        help="runtime in minutes (default 5)")
    parser.add_argument("--out", default="user_data/ws_validation",
                        help="output root directory")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR,
                        help="instrument-master cache (default: derived from "
                             f"TradingConfig.store_dir = {DEFAULT_CACHE_DIR})")
    args = parser.parse_args()

    run_id = datetime.now(tz=IST).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    symbols = load_symbols(Path(args.symbols_file), args.symbols)
    config = SmartApiConfig.from_env()
    instruments = SmartApiInstruments(config.instruments_url,
                                      cache_dir=Path(args.cache_dir))
    token_by_symbol, unresolved = resolve_tokens(instruments, symbols)
    symbol_by_token = {t: s for s, t in token_by_symbol.items()}
    if unresolved:
        print(f"unresolved symbols (skipped): {unresolved}")
    if not token_by_symbol:
        print("no symbols resolved to tokens - aborting")
        return 2

    try:
        session = SmartApiSession(config).ensure()
    except Exception as exc:
        print(f"SmartAPI login failed: {exc}")
        return 2
    jwt = getattr(session.client, "access_token", None)
    feed_token = session.feed_token
    if not jwt or not feed_token:
        print("login succeeded but jwt/feed token missing - aborting")
        return 2

    from SmartApi.smartWebSocketV2 import SmartWebSocketV2

    col = Collector(out_dir / "packets.csv", symbol_by_token)
    conn = {"opens": 0, "closes": 0, "errors": [], "requested": args.symbols,
            "started_utc": datetime.now(tz=timezone.utc).isoformat()}
    tokens = sorted(symbol_by_token)
    sub_request = [{"exchangeType": NSE_CM_EXCHANGE_TYPE, "tokens": tokens}]

    sws = SmartWebSocketV2(jwt, config.api_key, config.client_code,
                           feed_token, max_retry_attempt=3)

    def on_open(wsapp):
        conn["opens"] += 1
        sws.subscribe(f"wsval-{run_id}", QUOTE_MODE, sub_request)
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

    runtime_s = args.minutes * 60.0
    started = time.time()
    thread = threading.Thread(target=sws.connect, daemon=True,
                              name="smartapi-ws")
    thread.start()

    try:
        while time.time() - started < runtime_s:
            time.sleep(1.0)
            col.flush()
            elapsed = int(time.time() - started)
            if elapsed and elapsed % 30 == 0:
                print(f"  t+{elapsed:>4}s packets={col.total_packets}")
    except KeyboardInterrupt:
        print("interrupted - producing report from data so far")
    finally:
        try:
            sws.close_connection()
        except Exception as exc:
            print(f"close_connection: {exc}")
        thread.join(timeout=10)
        col.close()

    actual_runtime = time.time() - started
    subscribed = sorted(token_by_symbol)
    summary = summarize(col, subscribed, unresolved, actual_runtime, conn)
    verdict, reasons = conclude(summary)
    summary["conclusion"] = {"verdict": verdict, "evidence": reasons}

    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")
    write_candles_csv(out_dir / "candles.csv", col)
    write_report(out_dir / "report.txt", summary, verdict, reasons)
    print(f"\nartifacts: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
