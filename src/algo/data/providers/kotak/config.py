"""KotakNeoConfig - configuration for the Kotak Neo provider, from environment.

Every credential is read from an environment variable; NONE is ever hardcoded,
and secret fields are marked ``repr=False`` so they cannot leak into logs or
tracebacks. The TOTP can be supplied either as a live 6-digit code
(``KOTAK_NEO_TOTP``) or, for automation, as a base32 secret
(``KOTAK_NEO_TOTP_SECRET``) from which the current code is generated with pyotp.

Environment variables:
    KOTAK_NEO_CONSUMER_KEY     (required)  Trade-API token from the Neo app/web.
    KOTAK_NEO_CONSUMER_SECRET  (optional)  Consumer secret, if your app uses one.
    KOTAK_NEO_MOBILE           (required)  Registered mobile (with country code).
    KOTAK_NEO_UCC              (required)  Unique Client Code.
    KOTAK_NEO_MPIN             (required)  6-digit MPIN.
    KOTAK_NEO_TOTP             (optional)  Current 6-digit TOTP.
    KOTAK_NEO_TOTP_SECRET      (optional)  Base32 TOTP secret (pyotp generates).
    KOTAK_NEO_ENVIRONMENT      (optional)  'prod' (default) or 'uat'.
    KOTAK_NEO_FIN_KEY          (optional)  neo_fin_key, tracking only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


class KotakConfigError(RuntimeError):
    """Raised when required Kotak Neo configuration is missing."""


@dataclass
class KotakNeoConfig:
    # Non-secret
    environment: str = "prod"
    # Secrets - repr=False so they never appear in logs/reprs/tracebacks.
    consumer_key: Optional[str] = field(default=None, repr=False)
    consumer_secret: Optional[str] = field(default=None, repr=False)
    mobile_number: Optional[str] = field(default=None, repr=False)
    ucc: Optional[str] = field(default=None, repr=False)
    mpin: Optional[str] = field(default=None, repr=False)
    totp: Optional[str] = field(default=None, repr=False)
    totp_secret: Optional[str] = field(default=None, repr=False)
    neo_fin_key: Optional[str] = field(default=None, repr=False)

    @classmethod
    def from_env(cls, env: Optional[dict] = None) -> "KotakNeoConfig":
        env = env if env is not None else os.environ
        return cls(
            environment=env.get("KOTAK_NEO_ENVIRONMENT", "prod"),
            consumer_key=env.get("KOTAK_NEO_CONSUMER_KEY"),
            consumer_secret=env.get("KOTAK_NEO_CONSUMER_SECRET"),
            mobile_number=env.get("KOTAK_NEO_MOBILE"),
            ucc=env.get("KOTAK_NEO_UCC"),
            mpin=env.get("KOTAK_NEO_MPIN"),
            totp=env.get("KOTAK_NEO_TOTP"),
            totp_secret=env.get("KOTAK_NEO_TOTP_SECRET"),
            neo_fin_key=env.get("KOTAK_NEO_FIN_KEY"),
        )

    def require_login(self) -> None:
        """Raise KotakConfigError if anything needed to log in is absent."""
        missing = [name for name, value in (
            ("KOTAK_NEO_CONSUMER_KEY", self.consumer_key),
            ("KOTAK_NEO_MOBILE", self.mobile_number),
            ("KOTAK_NEO_UCC", self.ucc),
            ("KOTAK_NEO_MPIN", self.mpin),
        ) if not value]
        if not self.totp and not self.totp_secret:
            missing.append("KOTAK_NEO_TOTP or KOTAK_NEO_TOTP_SECRET")
        if missing:
            raise KotakConfigError(
                "missing Kotak Neo configuration: " + ", ".join(missing)
                + ". Set them in the environment (never in code).")

    def resolve_totp(self) -> str:
        """Return a current 6-digit TOTP: the provided code, else generated."""
        if self.totp:
            return self.totp
        if self.totp_secret:
            try:
                import pyotp
            except ImportError as exc:  # pragma: no cover - env-dependent
                raise KotakConfigError(
                    "KOTAK_NEO_TOTP_SECRET is set but pyotp is not installed; "
                    "`pip install pyotp` or provide KOTAK_NEO_TOTP directly."
                ) from exc
            return pyotp.TOTP(self.totp_secret).now()
        raise KotakConfigError(
            "no TOTP available: set KOTAK_NEO_TOTP or KOTAK_NEO_TOTP_SECRET.")
