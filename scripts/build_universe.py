"""Generate the trading universe file from the SmartAPI instrument master.

    .venv/Scripts/python scripts/build_universe.py            # NIFTY 100
    .venv/Scripts/python scripts/build_universe.py --out u.txt --index nifty50
    .venv/Scripts/python scripts/build_universe.py --index nifty500 \
        --out nifty500.txt                                   # NIFTY 500

Writes one NSE symbol per line (the format download_history / run_measurement /
run_paper all consume). Filters to ACTIVE NSE cash equities present in the
official master, so a symbol that is delisted or renamed simply never enters
the universe.

The index constituent list is a static, dated snapshot: the official master
carries no index membership, and the SmartAPI SDK exposes no constituents
endpoint. Rather than invent one, membership is declared with its as-of date
and intersected with the live master (so any symbol NSE has since removed drops
out automatically, and the mismatch is reported). NIFTY 50/100 are small enough
to keep inline; NIFTY 500 is the committed, dated NSE snapshot CSV under
``user_data/universe/`` (real exchange data, not fabricated) - its Industry
column also seeds the instruments.sector map (D-032).
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from algo.core.logging import configure
from algo.data.providers.smartapi import SmartApiConfig, SmartApiInstruments

#: Committed, dated NSE snapshot for NIFTY 500 (Company,Industry,Symbol,Series,ISIN).
NIFTY500_CSV = Path("user_data/universe/ind_nifty500_2026-07.csv")


def _nifty500_from_csv(path: Path) -> list:
    """Symbols of the EQ-series constituents from the committed NSE snapshot."""
    with open(path, encoding="utf-8-sig") as fh:
        return [row["Symbol"].strip() for row in csv.DictReader(fh)
                if row.get("Series", "").strip() == "EQ"]

# NIFTY 100 constituents (NSE index snapshot, as of 2026-07). Static by
# necessity - see the module docstring. Intersected with the live master.
NIFTY_50 = """
ADANIENT ADANIPORTS APOLLOHOSP ASIANPAINT AXISBANK BAJAJ-AUTO BAJFINANCE
BAJAJFINSV BEL BHARTIARTL BPCL BRITANNIA CIPLA COALINDIA DRREDDY EICHERMOT
GRASIM HCLTECH HDFCBANK HDFCLIFE HEROMOTOCO HINDALCO HINDUNILVR ICICIBANK
INDUSINDBK INFY ITC JIOFIN JSWSTEEL KOTAKBANK LT M&M MARUTI NESTLEIND NTPC
ONGC POWERGRID RELIANCE SBILIFE SBIN SHRIRAMFIN SUNPHARMA TATACONSUM
TATAMOTORS TATASTEEL TCS TECHM TITAN TRENT ULTRACEMCO WIPRO
""".split()

NIFTY_NEXT_50 = """
ABB ADANIENSOL ADANIGREEN ADANIPOWER AMBUJACEM DMART BAJAJHLDNG BANKBARODA
BERGEPAINT BOSCHLTD CANBK CHOLAFIN COLPAL DABUR DIVISLAB DLF GAIL GODREJCP
HAVELLS HAL ICICIGI ICICIPRULI INDIGO IOC IRCTC IRFC JINDALSTEL JSWENERGY
LICI LODHA LTIM MARICO MOTHERSON MRF MUTHOOTFIN NAUKRI PFC PIDILITIND PNB
POLYCAB RECLTD SHREECEM SIEMENS SRF TATAPOWER TORNTPHARM TVSMOTOR UNITDSPR
VBL ZYDUSLIFE
""".split()

INDICES = {
    "nifty50": NIFTY_50,
    "nifty100": NIFTY_50 + NIFTY_NEXT_50,
    "nifty500": None,       # loaded from the committed CSV snapshot
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", default="nifty100", choices=sorted(INDICES))
    parser.add_argument("--out", default="nifty100.txt")
    parser.add_argument("--csv", default=str(NIFTY500_CSV),
                        help="NSE snapshot CSV for --index nifty500")
    parser.add_argument("--cache-dir", default="user_data/data/nse/_instruments")
    args = parser.parse_args()
    configure(level=logging.WARNING)

    config = SmartApiConfig.from_env()
    instruments = SmartApiInstruments(config.instruments_url,
                                      cache_dir=args.cache_dir)
    master = instruments.ensure()            # no auth needed for the master
    listed = set(instruments.symbols())

    wanted = (_nifty500_from_csv(Path(args.csv)) if args.index == "nifty500"
              else INDICES[args.index])
    active = [s for s in wanted if s in listed]
    missing = [s for s in wanted if s not in listed]

    out = Path(args.out)
    out.write_text("\n".join(
        [f"# {args.index.upper()} universe - active NSE cash equities",
         f"# generated from the SmartAPI instrument master ({len(master)} "
         f"NSE -EQ symbols)", ""] + active) + "\n", encoding="utf-8")

    print(f"{args.index}: {len(active)}/{len(wanted)} symbols active in the "
          f"live master -> {out}")
    if missing:
        print(f"not in the master (delisted/renamed/index drift): {missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
