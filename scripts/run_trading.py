"""Run the production trading system (paper by default; live is disarmed).

    .venv/Scripts/python scripts/run_trading.py --once           # one cycle
    .venv/Scripts/python scripts/run_trading.py --loop           # live loop
    .venv/Scripts/python scripts/run_trading.py --config cfg.json

Live mode requires ALL THREE keys (mode=live in config + live_trading_enabled
+ env ALGO_ENABLE_LIVE=YES); without them the AngelOne adapter refuses to
place orders. This script NEVER auto-enables live.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from algo.core.logging import configure, get_logger
from algo.marketdata import for_provider
from algo.trading.config import TradingConfig
from algo.trading.engine import ProductionEngine
from algo.trading.session_setup import interactive_available, run_wizard

logger = get_logger("scripts.run_trading")


def build_engine(config: TradingConfig) -> ProductionEngine:
    """Wire the engine, and SAY which market-data mode it got.

    Paper trading is allowed to run offline against stored candles, but the
    operator must never have to deduce which mode is in force - running offline
    while believing the feed is live is how a session trades on stale bars.
    Preflight then verifies the symbol->token mapping before trading starts.

    The provider is wrapped as a :class:`MarketDataSource` and handed to the
    engine, which owns the whole subsystem from there. This script does not
    fetch, does not schedule, and holds no reference to a store.
    """
    session = instruments = source = None
    if config.is_live or config.mode == "paper":
        # a live provider is optional in paper mode; wire it only if creds exist
        try:
            from algo.data.providers.smartapi import SmartApiConfig, \
                build_provider
            missing = SmartApiConfig.from_env().missing()
            if missing:
                print(f"  ! no SmartAPI credentials ({', '.join(missing)}) - "
                      f"running OFFLINE on stored candles")
            else:
                provider = build_provider(
                    cache_dir=Path(config.store_dir) / "_instruments")
                session = provider.session
                instruments = provider.instruments
                source = for_provider(provider)
                print(f"  live market-data provider wired "
                      f"({source.name}; candles "
                      f"{source.capabilities.candle_symbols_per_request}/req, "
                      f"quotes "
                      f"{source.capabilities.quote_symbols_per_request}/req)")
        except Exception as exc:   # paper must run without a provider
            logger.warning("market-data provider could not be wired (%s) - "
                           "running OFFLINE on stored candles", exc)
            print(f"  ! market-data provider unavailable ({exc}) - "
                  f"running OFFLINE on stored candles")
    return ProductionEngine(config, session=session, instruments=instruments,
                            source=source)


def serve_dashboard(port: int, directory: str = "dashboard",
                    host: str = "0.0.0.0", show_qr: bool = True) -> None:
    """Serve the read-only dashboard on a background daemon thread.

    Binds to every interface by default so a phone on the same Wi-Fi can open
    it; pass ``--dashboard-host 127.0.0.1`` to restrict it to this laptop.

    It is a plain STATIC FILE SERVER for the dashboard folder (the UI plus the
    JSON snapshots the engine writes into it):

    * only GET/HEAD are served - every other method is refused, so nothing on
      the network can post, modify or delete anything;
    * it serves that one folder only (the handler rejects path traversal);
    * it exposes no control endpoint of any kind - there is no way to place an
      order, change configuration, or stop trading through it;
    * request logging is suppressed, otherwise a phone polling ten files every
      two seconds would bury the trading output.
    """
    import functools
    import http.server
    import socketserver
    import threading

    from algo.trading.network import describe, lan_addresses, qr_lines

    class ReadOnlyHandler(http.server.SimpleHTTPRequestHandler):
        """GET/HEAD only, and silent."""

        def do_POST(self):      # noqa: N802 - stdlib naming
            self.send_error(405, "This dashboard is read-only")

        do_PUT = do_DELETE = do_PATCH = do_POST

        def log_message(self, fmt, *args):
            pass                # keep the trading console readable

    handler = functools.partial(ReadOnlyHandler, directory=directory)

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    try:
        httpd = Server((host, port), handler)
    except OSError as exc:
        print(f"  ! dashboard server could not start on {host}:{port}: {exc}")
        print(f"    (is another copy already running? try "
              f"--dashboard-port {port + 1})")
        return
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    print("\n  Dashboard available at:")
    for line in describe(port, host=host):
        print(f"    {line}")

    if show_qr and host not in ("127.0.0.1", "localhost"):
        primary = next((a for a in lan_addresses() if a.primary), None)
        if primary is not None:
            # a QR code is a convenience: never let a rendering or console
            # encoding problem interfere with starting the trading engine
            try:
                lines = qr_lines(primary.url(port))
                if lines:
                    print(f"\n  Scan to open on your phone "
                          f"({primary.url(port)}):\n")
                    for line in lines:
                        print("    " + line)
                else:
                    print("    (tip: `pip install qrcode` to print a "
                          "scannable QR code here)")
            except Exception as exc:            # pragma: no cover
                print(f"    (QR code unavailable: {exc})")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="JSON TradingConfig (optional)")
    parser.add_argument("--mode", choices=["paper", "live"], default=None)
    parser.add_argument("--once", action="store_true", help="run one cycle")
    parser.add_argument("--loop", action="store_true", help="continuous loop")
    parser.add_argument("--interval", type=float, default=0.0,
                        help="fixed loop seconds; 0 = sleep to next bar close")
    parser.add_argument("--dashboard", action="store_true",
                        help="print a console snapshot and exit")
    parser.add_argument("--serve-dashboard", action="store_true",
                        help="also serve the web dashboard (recommended)")
    parser.add_argument("--dashboard-port", type=int, default=8787)
    parser.add_argument("--dashboard-host", default="0.0.0.0",
                        help="0.0.0.0 (default) makes the dashboard reachable "
                             "from phones on the same Wi-Fi; use 127.0.0.1 to "
                             "restrict it to this laptop")
    parser.add_argument("--no-qr", action="store_true",
                        help="do not print the QR code")
    parser.add_argument("--no-wizard", action="store_true",
                        help="skip the interactive session setup and use the "
                             "configured capital values as-is")
    args = parser.parse_args()
    configure(level=logging.INFO)

    data = {}
    if args.config and Path(args.config).exists():
        data = json.loads(Path(args.config).read_text())
    if args.mode:
        data["mode"] = args.mode
    config = TradingConfig.from_dict(data)

    print(f"mode={config.mode}  live_armed={config.live_armed()}")
    if args.serve_dashboard:
        serve_dashboard(args.dashboard_port, host=args.dashboard_host,
                        show_qr=not args.no_qr)
    engine = build_engine(config)

    # Session setup. Live mode needs a connected adapter first so the wizard
    # can offer the account's real cash; paper has nothing to read.
    if not args.no_wizard and interactive_available():
        if config.is_live:
            try:
                engine.adapter.connect()
            except Exception as exc:
                print(f"  ! could not connect to the broker for the balance "
                      f"({exc}) - you will be asked for the capital")
        choices = run_wizard(config, adapter=engine.adapter)
        if choices is None:
            return 0
        # session values are held in memory only - the config file on disk is
        # never rewritten, so today's numbers cannot become tomorrow's default
        config = choices.apply(config)
        engine = build_engine(config)
    elif not args.no_wizard:
        print("  (no terminal detected - using the configured capital values)")

    if not engine.startup():
        print("PREFLIGHT FAILED - trading will not start. See event log.")
        return 3

    if args.dashboard:
        print(engine.dashboard())
        return 0
    if args.once or not args.loop:
        # One shot has no loop to return to, so drive the pipeline to
        # completion here rather than leaving a bounded poll half-served.
        engine.marketdata.drain(held=[p.symbol for p
                                      in engine.portfolio.open_positions()])
        result = engine.tick()
        print(f"tick: due={result['due']} opened={result['opened']} "
              f"stats={result['stats']}")
        print(engine.dashboard())
        return 0

    # Continuous loop. ONE beat drives everything: engine.tick() polls the
    # market-data subsystem (bounded, never blocking), manages open risk, and
    # scans the timeframes whose data actually arrived. There is no second
    # loop and no separate fast tier - the previous design's live tier was an
    # independent poller with its own timer, which is precisely the duplicate
    # ownership this architecture removes.
    #
    # Outside the trading window the loop IDLES rather than exiting, so the
    # dashboard stays reachable (start it at 09:00 for a 09:15 open, or leave
    # it up to monitor from a phone). It ends the day by itself only after the
    # session has closed and no position remains.
    clock = engine.clock
    last_print = 0.0
    try:
        while True:
            now = clock.now()
            trading = clock.is_open(now) or engine.portfolio.open_count()
            if trading:
                result = engine.tick()
                # print the console dashboard when something happened, or at
                # most every 30s - a redraw per second is unreadable
                if result["due"] or time.monotonic() - last_print > 30:
                    print(engine.dashboard())
                    last_print = time.monotonic()
                # sleep to the next due moment, but never past work the rate
                # budget will allow us to send sooner
                nap = args.interval or min(
                    engine.marketdata.seconds_until_next(now),
                    max(0.2, float(engine.config.live_export_seconds)))
                time.sleep(max(0.05, nap))
                continue
            else:
                session_day = clock.is_session_day(now.date())
                before_open = (session_day
                               and now < clock.session_open(now.date()))
                if before_open:
                    opens_in = int((clock.session_open(now.date())
                                    - now).total_seconds())
                    print(f"  market opens in {opens_in // 60}m "
                          f"{opens_in % 60}s — waiting (dashboard live)")
                elif session_day:
                    print("  session closed and flat — ending the day")
                    break
                else:
                    print("  market closed (not a trading session) — "
                          "idling; press Ctrl-C to exit")
                # keep the dashboard fresh while idle so a phone shows live
                # status instead of a STALE banner
                if engine.exporter is not None:
                    engine.exporter.export()
                nap = args.interval or 30
            time.sleep(max(1.0, nap))
    except KeyboardInterrupt:
        logger.info("interrupted - persisting state and exiting")
    finally:
        engine.end_of_day()
        engine.adapter.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
