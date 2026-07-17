"""SmartApiSession - login, refresh, logout, profile against the official SDK.

Official flow (smartapi-python README + SDK source, verified):

    client = SmartConnect(api_key)
    data = client.generateSession(client_code, pin, totp)   # TOTP via pyotp
    ...  data["data"]["refreshToken"] / ["jwtToken"] / ["feedToken"]
    client.generateToken(refresh_token)                     # session refresh
    client.getProfile(refresh_token)                        # profile
    client.terminateSession(client_code)                    # logout

The SDK is imported lazily (optional dependency) and the client is injectable
(``client_factory``), so the whole session is exercised with a mock - no SDK
install, no credentials, no network. Secrets are never logged; error messages
carry SmartAPI's message text, never credential values.
"""

from __future__ import annotations

from typing import Callable, Optional

from algo.core.logging import get_logger
from algo.data.providers.smartapi.config import SmartApiConfig

logger = get_logger("data.smartapi.session")


class SmartApiAuthError(RuntimeError):
    """Raised when SmartAPI login/refresh fails."""


def _default_client_factory(config: SmartApiConfig):
    """Build the real SDK client (lazy import of the optional dependency)."""
    try:
        from SmartApi import SmartConnect
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise SmartApiAuthError(
            "smartapi-python is not installed. Install the official SDK: "
            "pip install smartapi-python pyotp logzero websocket-client "
            "pycryptodome") from exc
    return SmartConnect(api_key=config.api_key)


def _check(response, action: str) -> dict:
    """Validate a SmartAPI {'status': bool, ...} response; return its data."""
    if not isinstance(response, dict):
        raise SmartApiAuthError(f"{action}: unexpected response "
                                f"({type(response).__name__})")
    if not response.get("status", False):
        message = response.get("message") or response.get("errorcode") or "?"
        raise SmartApiAuthError(f"{action} failed: {message}")
    return response.get("data") or {}


class SmartApiSession:
    def __init__(self, config: SmartApiConfig,
                 client_factory: Optional[Callable] = None) -> None:
        self.config = config
        self._client_factory = client_factory or _default_client_factory
        self._client = None
        self.logged_in = False
        self.refresh_token: Optional[str] = None
        self.feed_token: Optional[str] = None

    # ------------------------------------------------------------- accessors

    @property
    def client(self):
        if self._client is None or not self.logged_in:
            raise SmartApiAuthError("not logged in - call login() first")
        return self._client

    def ensure(self) -> "SmartApiSession":
        """Login if not already logged in (idempotent)."""
        return self if self.logged_in else self.login()

    # ------------------------------------------------------------- lifecycle

    def login(self) -> "SmartApiSession":
        self.config.require_login()
        client = self._client_factory(self.config)
        data = _check(client.generateSession(
            self.config.client_code, self.config.pin,
            self.config.resolve_totp()), "generateSession")
        self.refresh_token = data.get("refreshToken")
        self.feed_token = data.get("feedToken")
        self._client = client
        self.logged_in = True
        logger.info("SmartAPI session established")   # never log tokens
        return self

    def refresh(self) -> "SmartApiSession":
        """Refresh the JWT using the stored refresh token."""
        if self._client is None or not self.refresh_token:
            raise SmartApiAuthError("cannot refresh - no active session")
        data = _check(self._client.generateToken(self.refresh_token),
                      "generateToken")
        self.refresh_token = data.get("refreshToken") or self.refresh_token
        logger.info("SmartAPI session refreshed")
        return self

    def profile(self) -> dict:
        """The account profile (verifies the session end-to-end)."""
        return _check(self.client.getProfile(self.refresh_token), "getProfile")

    def logout(self) -> None:
        if self._client is not None and self.logged_in:
            try:
                self._client.terminateSession(self.config.client_code)
                logger.info("SmartAPI session terminated")
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning("SmartAPI logout error: %s", exc)
        self.logged_in = False
        self._client = None
        self.refresh_token = None
        self.feed_token = None
