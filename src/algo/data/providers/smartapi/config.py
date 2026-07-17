"""SmartApiConfig - Angel One SmartAPI configuration, from the environment only.

Credentials are read from environment variables (populated from a local .env
via ``algo.core.config.load_env_file``). NOTHING is ever hardcoded; secret
fields are ``repr=False`` so they cannot leak into logs, reprs, or tracebacks.
Missing credentials are reported by NAME with where to obtain them - never by
value.

Environment variables (see .env.example for portal-by-portal provenance):
    SMARTAPI_API_KEY        (required)  the app's API key
    SMARTAPI_CLIENT_CODE    (required)  Angel One client code (login id)
    SMARTAPI_PIN            (required)  login PIN
    SMARTAPI_TOTP_SECRET    (required*) base32 TOTP secret (pyotp generates)
    SMARTAPI_TOTP           (optional*) a current 6-digit code instead
    SMARTAPI_INSTRUMENTS_URL (optional) scrip-master override
        * one of SMARTAPI_TOTP_SECRET / SMARTAPI_TOTP must be present to log in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

#: Official instrument master (documented by SmartAPI).
DEFAULT_INSTRUMENTS_URL = ("https://margincalculator.angelbroking.com"
                           "/OpenAPI_File/files/OpenAPIScripMaster.json")


class SmartApiConfigError(RuntimeError):
    """Raised when required SmartAPI configuration is missing."""


@dataclass
class SmartApiConfig:
    api_key: Optional[str] = field(default=None, repr=False)
    client_code: Optional[str] = field(default=None, repr=False)
    pin: Optional[str] = field(default=None, repr=False)
    totp_secret: Optional[str] = field(default=None, repr=False)
    totp: Optional[str] = field(default=None, repr=False)
    instruments_url: str = DEFAULT_INSTRUMENTS_URL

    @classmethod
    def from_env(cls, env: Optional[dict] = None,
                 env_file: Optional[str] = ".env") -> "SmartApiConfig":
        """Build from the environment, loading ``env_file`` first if given."""
        if env is None and env_file is not None:
            from algo.core.config import load_env_file
            load_env_file(env_file)
        env = env if env is not None else os.environ
        return cls(
            api_key=env.get("SMARTAPI_API_KEY") or None,
            client_code=env.get("SMARTAPI_CLIENT_CODE") or None,
            pin=env.get("SMARTAPI_PIN") or None,
            totp_secret=env.get("SMARTAPI_TOTP_SECRET") or None,
            totp=env.get("SMARTAPI_TOTP") or None,
            instruments_url=env.get("SMARTAPI_INSTRUMENTS_URL")
            or DEFAULT_INSTRUMENTS_URL,
        )

    def has_credentials(self) -> bool:
        return not self.missing()

    def missing(self) -> list:
        """Names (never values) of whatever is absent for a login."""
        absent = [name for name, value in (
            ("SMARTAPI_API_KEY", self.api_key),
            ("SMARTAPI_CLIENT_CODE", self.client_code),
            ("SMARTAPI_PIN", self.pin),
        ) if not value]
        if not self.totp_secret and not self.totp:
            absent.append("SMARTAPI_TOTP_SECRET (or SMARTAPI_TOTP)")
        return absent

    def require_login(self) -> None:
        absent = self.missing()
        if absent:
            raise SmartApiConfigError(
                "missing SmartAPI credentials: " + ", ".join(absent)
                + ". Create a local .env from .env.example (never commit it); "
                  "values come from smartapi.angelone.in (see .env.example).")

    def resolve_totp(self) -> str:
        """Current 6-digit TOTP: the provided code, else generated via pyotp."""
        if self.totp:
            return self.totp
        if self.totp_secret:
            try:
                import pyotp
            except ImportError as exc:  # pragma: no cover - env-dependent
                raise SmartApiConfigError(
                    "SMARTAPI_TOTP_SECRET is set but pyotp is not installed: "
                    "pip install pyotp") from exc
            return pyotp.TOTP(self.totp_secret).now()
        raise SmartApiConfigError(
            "no TOTP available: set SMARTAPI_TOTP_SECRET or SMARTAPI_TOTP.")
