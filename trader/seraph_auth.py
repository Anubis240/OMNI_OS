"""Stable Seraph authentication interface; OAuth implementation follows in W3.P2."""

import base64
import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Callable, Literal
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

from core import settings_store

# Log event names only; never keys, tokens, authorization codes or PKCE verifiers.
logger = logging.getLogger(__name__)

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

_TOKEN_FIELDS = ("access_token", "access_expires_at", "refresh_token", "refresh_issued_at", "scope")
_KEY_FIELDS = ("api_key", "api_key_id", "api_key_prefix", "api_key_name", "api_key_created_at", "api_key_source")


def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


class _LoopbackListener:
    """One-shot callback receiver. Wait without holding the SeraphAuth lock."""

    def __init__(self, state: str) -> None:
        self.event = threading.Event()
        self.code: str | None = None
        self.error: str | None = None
        self._accepted = False
        listener = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_GET(self):
                try:
                    parsed = urlsplit(self.path)
                    if parsed.path != CALLBACK_PATH:
                        self._reply(404)
                        return
                    if listener._accepted:
                        self._reply(410)
                        return
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    states = query.get("state", [])
                    if len(states) != 1 or not hmac.compare_digest(states[0].encode("utf-8"), state.encode("utf-8")):
                        self._reply(400)
                        return
                    codes = query.get("code", [])
                    errors = query.get("error", [])
                    if not ((len(codes) == 1 and codes[0] and not errors)
                            or (len(errors) == 1 and errors[0] and not codes)):
                        self._reply(400)
                        return
                    listener._accepted = True
                    listener.code = codes[0] if codes else None
                    listener.error = errors[0] if errors else None
                    self._reply(200)
                    listener.event.set()
                except Exception:
                    listener.error = "callback_failed"
                    listener.event.set()
                    logger.warning("oauth_callback_failed")

            def _reply(self, status):
                body = b"<!doctype html><html><body>You may close this window.</body></html>"
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

        self._server = HTTPServer((LOOPBACK_HOST, 0), Handler)
        self.port = self._server.server_port
        self.redirect_uri = f"http://{LOOPBACK_HOST}:{self.port}{CALLBACK_PATH}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def wait(self, timeout: float = LOGIN_TIMEOUT_S) -> bool:
        return self.event.wait(timeout)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()


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
        self._issuer = issuer
        self._resource = resource
        self._scopes = scopes
        self._settings_key = settings_key
        self._clock = clock
        self._needs_login = False
        self._last_error: str | None = None
        # The future login implementation must reset this before each flow.
        self._registrations_this_login = 0

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
        return self._load().get("api_key") or None

    def remint_api_key(self) -> str | None:
        """Reissue the device API key using the OAuth session when possible."""
        raise NotImplementedError("W3.P2 will implement remint_api_key")

    def set_manual_api_key(self, key: str) -> None:
        """Store a user-supplied API key and select manual authentication mode."""
        if not isinstance(key, str) or not key.strip():
            raise ValueError("API key must be a non-empty string")
        with self._lock:
            if not key.startswith(API_KEY_PREFIX):
                logger.warning("manual_api_key_unrecognized_prefix")

            def mutate(block):
                block.update(dict.fromkeys(_TOKEN_FIELDS + _KEY_FIELDS))
                block.update(api_key=key, api_key_source="manual", api_key_prefix=key[:12],
                             api_key_created_at=self._clock(), api_key_scopes=[])

            self._persist(mutate)
            self._needs_login = False
            self._last_error = None

    def disconnect_device(self) -> None:
        """Disconnect the device, revoking credentials and clearing local authentication."""
        raise NotImplementedError("W3.P2 will implement disconnect_device")

    def status(self) -> AuthStatus:
        """Return a pure, secret-free snapshot without network or persistence writes."""
        block = self._load()
        mode = "none"
        if block.get("api_key"):
            if block.get("api_key_source") == "oauth":
                mode = "api_key_oauth"
            elif block.get("api_key_source") == "manual":
                mode = "api_key_manual"
        try:
            secure_storage = settings_store._dpapi_available()
        except (AttributeError, TypeError):
            secure_storage = True
        return AuthStatus(
            mode=mode,
            api_key_prefix=block.get("api_key_prefix"),
            subject=block.get("subject"),
            org_id=block.get("org_id"),
            api_key_created_at=block.get("api_key_created_at"),
            api_key_scopes=tuple(block.get("api_key_scopes") or ()),
            needs_login=self._needs_login,
            secure_storage=secure_storage,
            last_error=self._last_error,
        )

    def _load(self) -> dict:
        block = settings_store.load_settings().get(self._settings_key, {})
        return block if isinstance(block, dict) else {}

    def _persist(self, mutator) -> None:
        # Lock order is always SeraphAuth first, then settings_store._SETTINGS_LOCK.
        with self._lock:
            def update(settings):
                block = settings.get(self._settings_key)
                if not isinstance(block, dict):
                    block = deepcopy(settings_store.DEFAULT_SETTINGS.get(SETTINGS_KEY, {}))
                    settings[self._settings_key] = block
                mutator(block)
            settings_store.update_settings(update)

    def _device_id(self) -> str:
        with self._lock:
            def ensure(block):
                value = block.get("device_id")
                if not isinstance(value, str) or not DEVICE_ID_RE.fullmatch(value):
                    block["device_id"] = secrets.token_urlsafe(24)

            value = self._load().get("device_id")
            if isinstance(value, str) and DEVICE_ID_RE.fullmatch(value):
                return value
            # Recheck under the settings lock to avoid racing another instance.
            result = []
            def mutate(block):
                ensure(block)
                result.append(block.get("device_id"))
            self._persist(mutate)
            return result[0]

    def _clear_tokens(self) -> None:
        self._persist(lambda block: block.update(dict.fromkeys(_TOKEN_FIELDS)))

    def _clear_api_key(self) -> None:
        def mutate(block):
            block.update(dict.fromkeys(_KEY_FIELDS))
            block["api_key_scopes"] = []
        self._persist(mutate)

    def _clear_session(self) -> None:
        def mutate(block):
            block.update(dict.fromkeys(_TOKEN_FIELDS + _KEY_FIELDS + ("subject", "org_id")))
            block["api_key_scopes"] = []
        self._persist(mutate)

    def _valid_metadata(self, metadata: dict) -> bool:
        if metadata.get("issuer") != self._issuer:
            return False
        origin = urlsplit(self._issuer)
        for name in DEFAULT_ENDPOINTS:
            value = metadata.get(name)
            if not isinstance(value, str):
                return False
            endpoint = urlsplit(value)
            if (endpoint.scheme != "https" or endpoint.scheme != origin.scheme
                    or endpoint.netloc != origin.netloc or endpoint.username or endpoint.password):
                return False
        return True

    def _discover_metadata(self) -> dict:
        fallback = {name: self._issuer.rstrip("/") + path for name, path in DEFAULT_ENDPOINTS.items()}
        fallback["issuer"] = self._issuer
        # Even fallback endpoints must never downgrade transport security.
        if not self._valid_metadata(fallback):
            raise ValueError("OAuth issuer must use HTTPS")
        block = self._load()
        fetched = block.get("metadata_fetched_at")
        try:
            if (isinstance(fetched, (int, float)) and 0 <= self._clock() - fetched < METADATA_TTL_S
                    and self._valid_metadata(block)):
                return {name: block.get(name) for name in (*DEFAULT_ENDPOINTS, "issuer")}
            response = (self.session or requests).get(
                self._issuer.rstrip("/") + "/.well-known/oauth-authorization-server",
                timeout=HTTP_TIMEOUT_S, allow_redirects=False)
            if response.status_code != 200:
                return fallback
            metadata = response.json()
            if not isinstance(metadata, dict) or not self._valid_metadata(metadata):
                logger.warning("oauth_metadata_rejected")
                return fallback
        except (requests.RequestException, ValueError, TypeError):
            logger.warning("oauth_metadata_unavailable")
            return fallback
        endpoints = {name: metadata.get(name) for name in (*DEFAULT_ENDPOINTS, "issuer")}
        self._persist(lambda current: current.update(endpoints, metadata_fetched_at=self._clock()))
        return endpoints

    def _probe_client(self) -> bool:
        try:
            response = (self.session or requests).get(
                self._issuer.rstrip("/") + CLIENT_INFO_PATH,
                params={"client_id": self._load().get("client_id")},
                timeout=HTTP_TIMEOUT_S, allow_redirects=False)
            return response.status_code != 404
        except requests.RequestException:
            logger.warning("oauth_client_probe_unavailable")
            return True

    def _register_client(self, redirect_uri) -> str | None:
        with self._lock:
            if self._registrations_this_login >= MAX_REGISTRATIONS_PER_LOGIN:
                return None
            self._registrations_this_login += 1
            try:
                endpoint = self._discover_metadata().get("registration_endpoint")
                response = (self.session or requests).post(endpoint, json={
                    "client_name": CLIENT_NAME, "redirect_uris": [redirect_uri],
                    "token_endpoint_auth_method": "none",
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                }, timeout=HTTP_TIMEOUT_S, allow_redirects=False)
                if response.status_code != 201:
                    return None
                data = response.json()
                client_id = data.get("client_id") if isinstance(data, dict) else None
                if not isinstance(client_id, str) or not CLIENT_ID_RE.fullmatch(client_id):
                    return None
            except (requests.RequestException, ValueError, TypeError):
                logger.warning("oauth_registration_failed")
                return None
            self._persist(lambda block: block.update(
                client_id=client_id, client_id_issued_at=data.get("client_id_issued_at", self._clock()),
                registered_redirect_uri=redirect_uri))
            return client_id

    def _ensure_client(self, redirect_uri) -> str | None:
        with self._lock:
            block = self._load()
            current = urlsplit(redirect_uri)
            registered = urlsplit(block.get("registered_redirect_uri") or "")
            # RFC 8252 loopback redirects ignore the ephemeral port. Register the
            # portless URI; compare scheme, hostname and path, never the port.
            compatible = ((current.scheme, current.hostname, current.path)
                          == (registered.scheme, registered.hostname, registered.path))
            client_id = block.get("client_id")
            if client_id and self._probe_client() and compatible:
                return client_id
            return self._register_client(f"{current.scheme}://{current.hostname}{current.path}")

    def _pkce_pair(self) -> tuple[str, str]:
        verifier = secrets.token_urlsafe(64)
        return verifier, _pkce_challenge(verifier)

    def _new_state(self) -> str:
        return secrets.token_urlsafe(32)

    def _build_authorize_url(self, *, client_id, redirect_uri, state, challenge) -> str:
        endpoint = self._discover_metadata().get("authorization_endpoint")
        query = urlencode({"response_type": "code", "client_id": client_id,
                           "redirect_uri": redirect_uri, "state": state,
                           "code_challenge": challenge, "code_challenge_method": "S256",
                           "scope": self._scopes, "resource": self._resource})
        return endpoint + ("&" if "?" in endpoint else "?") + query

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
