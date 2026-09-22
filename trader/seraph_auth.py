"""Stable Seraph authentication interface; OAuth implementation follows in W3.P2."""

import re
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Literal

if TYPE_CHECKING:
    import requests


SERAPH_ISSUER = "https://seraph.kondux.io"
SERAPH_API_RESOURCE = "https://seraph.kondux.io/api"  # Access JWT aud; never sent to /mcp.
SERAPH_MCP_URL = "https://seraph.kondux.io/mcp"
SERAPH_SCOPES = "api-keys:write wallet:execute offline_access"
SERAPH_API_KEYS_PATH = "/api/desktop/api-keys"
SERAPH_CONTROL_PLANE_URL = "https://mcp-firewall-control-plane-api.teamkondux.workers.dev"
SERAPH_WALLET_CONSOLE_URL = "https://seraph.kondux.io/wallet"
API_KEY_PREFIX = "mcfw_"
CLIENT_NAME = "Omni-OS Trader"
LOOPBACK_HOST = "127.0.0.1"
CALLBACK_PATH = "/callback"
REFRESH_SKEW_S = 60
REFRESH_DEDUP_S = 30
REMINT_DEDUP_S = 30
MAX_REGISTRATIONS_PER_LOGIN = 1
DEFAULT_EXPIRES_IN_S = 900
SETTINGS_KEY = "seraph_auth"
LOGIN_TIMEOUT_S = 300
HTTP_TIMEOUT_S = 15
MINT_TIMEOUT_S = 20
REVOKE_TIMEOUT_S = 10
DISCONNECT_TIMEOUT_S = 10
METADATA_TTL_S = 86400
CLIENT_INFO_PATH = "/client-info"
CLIENT_ID_RE = re.compile(r"^mcp_[A-Za-z0-9_-]{16,128}$")
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
DEFAULT_ENDPOINTS = {"authorization_endpoint": "/authorize", "token_endpoint": "/token", "registration_endpoint": "/register", "revocation_endpoint": "/revoke"}


@dataclass(frozen=True)
class AuthStatus:
    mode: Literal["api_key_oauth", "api_key_manual", "api_key_legacy", "none"]
    api_key_prefix: str | None  # Display prefix only (12 characters), never the key.
    subject: str | None
    org_id: str | None
    api_key_created_at: float | None
    api_key_scopes: tuple[str, ...]
    needs_login: bool
    secure_storage: bool = True
    last_error: str | None = None


@dataclass(frozen=True)
class AuthResult:
    ok: bool
    error: str | None = None
    error_description: str | None = None
    status: AuthStatus | None = None


class SeraphAuth:
    def __init__(self, *, issuer=SERAPH_ISSUER, resource=SERAPH_API_RESOURCE, scopes=SERAPH_SCOPES, api_keys_url: str | None = None,
                 settings_key=SETTINGS_KEY, session: "requests.Session | None" = None, opener: Callable[[str], bool] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        """Store configuration without network access or persistent-storage I/O."""
        self.issuer = issuer
        self.resource = resource
        self.scopes = scopes
        self.api_keys_url = (
            SERAPH_CONTROL_PLANE_URL + SERAPH_API_KEYS_PATH
            if api_keys_url is None else api_keys_url
        )
        self.settings_key = settings_key
        self.session = session
        self.opener = opener
        self.clock = clock
        self._lock = threading.RLock()

    def __repr__(self) -> str:
        """Return a secret-free description of the authentication mode."""
        # Never include API keys, tokens, secrets, or configuration in repr.
        return f"SeraphAuth(mode={self.status().mode!r})"

    def login(self, on_status: Callable[[str], None] | None = None, timeout_s: int = LOGIN_TIMEOUT_S) -> AuthResult:
        """Run OAuth login and device-key issuance, reporting progress until timeout."""
        raise NotImplementedError("W3.P2 will implement login")

    def cancel_login(self) -> None:
        """Cancel an in-progress login flow."""
        raise NotImplementedError("W3.P2 will implement cancel_login")

    def get_api_key(self) -> str | None:
        """Return the available device API key, or None when unavailable."""
        raise NotImplementedError("W3.P2 will implement get_api_key")

    def remint_api_key(self) -> str | None:
        """Reissue the device API key using the OAuth session when possible."""
        raise NotImplementedError("W3.P2 will implement remint_api_key")

    def set_manual_api_key(self, key: str) -> None:
        """Store a user-supplied API key and select manual authentication mode."""
        raise NotImplementedError("W3.P2 will implement set_manual_api_key")

    def disconnect_device(self) -> None:
        """Disconnect the device, revoking credentials and clearing local authentication."""
        raise NotImplementedError("W3.P2 will implement disconnect_device")

    def status(self) -> AuthStatus:
        """Return a disconnected, secret-free authentication snapshot in this skeleton."""
        return AuthStatus(
            mode="none",
            api_key_prefix=None,
            subject=None,
            org_id=None,
            api_key_created_at=None,
            api_key_scopes=(),
            needs_login=False,
            secure_storage=True,
            last_error=None,
        )

    def is_logged_in(self) -> bool:
        """Report whether the current API key comes from an OAuth login."""
        return self.status().mode == "api_key_oauth"

    def _get_access_token(self, force_refresh: bool = False) -> str | None:
        """Obtain an API-resource access token, refreshing forcibly when requested."""
        raise NotImplementedError("W3.P2 will implement _get_access_token")

    def invalidate_session(self) -> None:
        """Invalidate the current OAuth session so authentication can be renewed."""
        raise NotImplementedError("W3.P2 will implement invalidate_session")


_default_auth: SeraphAuth | None = None
_default_auth_lock = threading.Lock()


def get_default_auth() -> SeraphAuth:
    """Lazily create and return the module singleton under a lock."""
    global _default_auth
    with _default_auth_lock:
        if _default_auth is None:
            _default_auth = SeraphAuth()
        return _default_auth


def abbreviate_subject(subject: str | None) -> str:
    """Return a compact subject label, or an em dash for an absent subject."""
    if not subject:
        return "—"
    # Keep strings up to 17 characters; otherwise keep 12 + ellipsis + last 5.
    # The first 12 preserve "did:privy:" and two identifier characters.
    if len(subject) <= 17:
        return subject
    return f"{subject[:12]}…{subject[-5:]}"


def format_identity(status: AuthStatus) -> str:
    """Format display-only identity; legacy keys receive an explicit legacy label."""
    if status.mode == "none":
        return "not connected"
    # Missing prefixes degrade to "key" without exposing other credential data.
    key_label = f"key {status.api_key_prefix}" if status.api_key_prefix else "key"
    if status.mode == "api_key_oauth":
        return f"{key_label} (signed in as {abbreviate_subject(status.subject)})"
    if status.mode == "api_key_manual":
        return f"{key_label} (manual)"
    return f"{key_label} (legacy)"
