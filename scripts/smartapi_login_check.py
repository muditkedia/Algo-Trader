"""Verify SmartAPI credentials and connectivity end to end.

    .venv/Scripts/python scripts/smartapi_login_check.py

Without credentials: reports exactly which .env variables are missing and
where to obtain them (exit code 2) - no traceback, no secrets printed.
With credentials: instrument master -> login -> profile -> refresh -> logout.
Secrets and tokens are never printed.
"""

from __future__ import annotations

import logging
import sys

from algo.core.logging import configure
from algo.data.providers.smartapi import (
    SmartApiConfig, SmartApiInstruments, SmartApiSession,
)


def main() -> int:
    configure(level=logging.INFO)
    config = SmartApiConfig.from_env()          # loads .env if present

    print("[1/5] instrument master (no auth needed)...")
    try:
        instruments = SmartApiInstruments(config.instruments_url)
        master = instruments.fetch()
        print(f"      OK - {len(master)} NSE equities "
              f"(e.g. {', '.join(instruments.symbols()[:5])} ...)")
    except Exception as exc:
        print(f"      FAILED - {exc}")
        return 1

    missing = config.missing()
    if missing:
        print("[2/5] credentials: MISSING ->", ", ".join(missing))
        print("      Create .env from .env.example (see the comments there "
              "for exactly where each value comes from in the Angel One "
              "portal). Nothing else is required.")
        return 2
    print("[2/5] credentials: present (values never printed)")

    session = SmartApiSession(config)
    try:
        print("[3/5] login (generateSession + TOTP)...")
        session.login()
        print("      OK - session established")

        print("[4/5] profile (getProfile)...")
        profile = session.profile()
        name = profile.get("name", "?")
        exchanges = profile.get("exchanges", [])
        print(f"      OK - account: {name}, exchanges: {exchanges}")

        print("[5/5] token refresh + logout...")
        session.refresh()
        session.logout()
        print("      OK")
        print("\nSMARTAPI LOGIN CHECK PASSED")
        return 0
    except Exception as exc:
        print(f"      FAILED - {exc}")
        try:
            session.logout()
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
