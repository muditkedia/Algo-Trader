"""KotakNeoSession - secure login and session handling for the Kotak Neo SDK.

Wraps the official two-step TOTP flow:

    client.totp_login(mobile_number, ucc, totp)   -> view token + sid
    client.totp_validate(mpin)                    -> trade token (session ready)

Credentials come only from ``KotakNeoConfig`` (environment); nothing is
hardcoded and secrets are never logged. The underlying ``neo_api_client.NeoAPI``
is imported lazily (optional dependency) and can be supplied via
``client_factory`` for tests, so this class is exercised entirely with a mock -
no SDK install, no real credentials.
"""

from __future__ import annotations

from typing import Callable, Optional

from algo.core.logging import get_logger
from algo.data.providers.kotak.config import KotakNeoConfig

logger = get_logger("data.kotak.session")


class KotakAuthError(RuntimeError):
    """Raised when Kotak Neo login/2FA fails."""


def _default_client_factory(config: KotakNeoConfig):
    """Build a real NeoAPI client (lazy import of the optional SDK)."""
    try:
        from neo_api_client import NeoAPI
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise KotakAuthError(
            "neo_api_client is not installed. Install the official SDK: "
            "pip install \"git+https://github.com/Kotak-Neo/"
            "Kotak-neo-api-v2.git#egg=neo_api_client\"") from exc
    return NeoAPI(environment=config.environment, access_token=None,
                  neo_fin_key=config.neo_fin_key,
                  consumer_key=config.consumer_key)


def _has_error(response) -> Optional[str]:
    """Extract an error message from a Kotak SDK response, if any."""
    if isinstance(response, dict):
        if "error" in response and response["error"]:
            err = response["error"]
            if isinstance(err, list) and err:
                return str(err[0].get("message", err[0]))
            return str(err)
        for key in ("Error", "Error Message"):
            if key in response:
                return str(response[key])
    return None


class KotakNeoSession:
    def __init__(self, config: KotakNeoConfig,
                 client_factory: Optional[Callable] = None) -> None:
        self.config = config
        self._client_factory = client_factory or _default_client_factory
        self._client = None
        self.logged_in = False

    @property
    def client(self):
        if self._client is None or not self.logged_in:
            raise KotakAuthError("not logged in - call login() first")
        return self._client

    def login(self) -> "KotakNeoSession":
        """Perform the two-step TOTP login. Idempotent once logged in."""
        if self.logged_in:
            return self
        self.config.require_login()
        client = self._client_factory(self.config)

        view = client.totp_login(
            mobile_number=self.config.mobile_number, ucc=self.config.ucc,
            totp=self.config.resolve_totp())
        err = _has_error(view)
        if err:
            raise KotakAuthError(f"totp_login failed: {err}")

        validated = client.totp_validate(mpin=self.config.mpin)
        err = _has_error(validated)
        if err:
            raise KotakAuthError(f"totp_validate failed: {err}")

        self._client = client
        self.logged_in = True
        logger.info("Kotak Neo session established (env=%s)",
                    self.config.environment)
        return self

    def logout(self) -> None:
        if self._client is not None and self.logged_in:
            try:
                self._client.logout()
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning("Kotak logout error: %s", exc)
        self.logged_in = False
        self._client = None
