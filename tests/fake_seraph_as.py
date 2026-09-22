"""LOCAL test fake: no real credentials or production secrets live here.

HTTPS uses a disposable self-signed certificate trusted via client_session().
SeraphAuth._valid_metadata requires HTTPS and same-origin endpoints, including
DEFAULT_ENDPOINTS before discovery performs any request. HTTP cannot use the
fallback. Patching that validation was rejected: it disables the very defense
these integration tests should exercise.

expire_access records all issued JWT jtis in expired_access_jtis; Part B must
consult that set in addition to JWT exp. Newly issued tokens remain valid.
"""

import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import ipaddress
import json
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import threading
import time
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
import requests
import uvicorn


CAP_TX_WEI = 20_000_000_000_000_000
CAP_DAY_WEI = 200_000_000_000_000_000
MAX_TX_PER_DAY = 20
GATE_TTL_MS = 180_000
SELECTOR_APPROVE = "0x095ea7b3"
SELECTOR_WETH_WITHDRAW = "0x2e1a7d4d"
SELECTOR_V3_EXACT_INPUT_SINGLE = "0x04e45aaf"
SELECTOR_V2_BUY = "0x7ff36ab5"
SELECTOR_V2_SELL = "0x18cbafe5"
FAKE_CHAINS = {
    1: {"v3": "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45", "v2": "0x7a250d5630b4cf539739df2c5dacb4c659f2488d", "weth": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"},
    10: {"v3": "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45", "v2": None, "weth": "0x4200000000000000000000000000000000000006"},
    130: {"v3": "0x73855d06de49d0fe4a9c42636ba96c62da12ff9c", "v2": None, "weth": "0x4200000000000000000000000000000000000006"},
    480: {"v3": "0x091ad9e2e6e5ed44c1c66db50e49a601f9f36cf6", "v2": None, "weth": "0x4200000000000000000000000000000000000006"},
    4663: {"v3": "0xcaf681a66d020601342297493863e78c959e5cb2", "v2": None, "weth": "0x0bd7d308f8e1639fab988df18a8011f41eacad73"},
    8453: {"v3": "0x2626664c2603336e57b271c5c0b26f421741e481", "v2": None, "weth": "0x4200000000000000000000000000000000000006"},
    42161: {"v3": "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45", "v2": None, "weth": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"},
}


def _jcs(value):
    """RFC 8785 subset: ASCII keys only, no floats. Code-point ordering of
    ASCII keys is identical to JCS's UTF-16 ordering."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _generate_self_signed_cert(tmpdir: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(hours=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                x509.DNSName("localhost")]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmpdir / "cert.pem", tmpdir / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                         serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    return cert_path, key_path


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class FakeSeraph:
    def __init__(self) -> None:
        self.app = FastAPI()
        self._base_url = ""
        self._tmpdir: Path | None = None
        self._cert_path: Path | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._stats = dict.fromkeys(("registrations", "token_calls", "refreshes",
                                    "mints", "deletes", "gates", "executes",
                                    "privy_calls", "mcp_401s"), 0)
        self._clients: dict[str, dict] = {}
        self._codes: dict[str, dict] = {}
        self._refresh_tokens: dict[str, dict] = {}
        self._revoked_families: set[str] = set()
        self._latest_family: str | None = None
        self._access_jtis: set[str] = set()
        self.expired_access_jtis: set[str] = set()
        self._api_keys: dict[str, dict] = {}
        self._signer = {"address": None, "granted_at": None, "external": None}
        self._mint_failure: int | None = None
        self._gates: dict = {}
        self._executions: dict = {}
        self._daily: dict = {}
        self._privy_idem: dict = {}
        self._privy_delay_ms: int = 0
        self._internal_secret: str = "fake-internal-secret"
        self._corrupt_privy_signature: bool = False
        self._pretrade_pending = False
        self._gate_decision_override = None
        self._privy_auth_key = ec.generate_private_key(ec.SECP256R1())
        self._privy_auth_pub = self._privy_auth_key.public_key()
        self._wrong_auth_key = ec.generate_private_key(ec.SECP256R1())
        self._install_auth_routes()
        self._install_control_plane_routes()
        self._install_wallet_routes()
        self._install_mcp_routes()

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def mcp_url(self) -> str:
        return self.base_url + "/mcp"

    @property
    def ca_bundle(self) -> str:
        if self._cert_path is None:
            raise RuntimeError("FakeSeraph has not started")
        return str(self._cert_path)

    def client_session(self) -> requests.Session:
        session = requests.Session()
        session.verify = self.ca_bundle
        return session

    def stats(self) -> dict:
        return self._stats.copy()

    def api_keys(self) -> list[dict]:
        return [deepcopy({k: v for k, v in record.items() if k != "key"})
                for record in self._api_keys.values()]

    def active_key_count(self) -> int:
        return sum(not record["revoked"] for record in self._api_keys.values())

    def signer_state(self) -> dict:
        return {"address": self._signer["address"],
                "signerGranted": self._signer["granted_at"] is not None,
                "signerGrantedAt": self._signer["granted_at"],
                "linkedExternalAddress": self._signer["external"]}

    def start(self) -> str:
        if self._thread is not None and self._thread.is_alive():
            return self.base_url
        self._tmpdir = Path(tempfile.mkdtemp(prefix="fake-seraph-")).resolve()
        try:
            self._cert_path, key_path = _generate_self_signed_cert(self._tmpdir)
            config = uvicorn.Config(self.app, host="127.0.0.1", port=0,
                                    log_level="error", ssl_certfile=self.ca_bundle,
                                    ssl_keyfile=str(key_path))
            self._server = uvicorn.Server(config)
            self._server.install_signal_handlers = lambda: None
            self._thread = threading.Thread(target=self._server.run, daemon=True)
            self._thread.start()
            deadline = time.monotonic() + 10
            while not self._server.started and time.monotonic() < deadline:
                if not self._thread.is_alive():
                    break
                time.sleep(0.01)
            if not self._server.started:
                raise RuntimeError("FakeSeraph failed to start")
            port = self._server.servers[0].sockets[0].getsockname()[1]
            self._base_url = f"https://127.0.0.1:{port}"
            return self.base_url
        except Exception as exc:
            self.stop()
            raise RuntimeError("FakeSeraph startup failed") from exc

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)
        if self._tmpdir is not None:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        self._cert_path = None

    def _make_jwt(self, resource: str, scope: str) -> str:
        now = int(time.time())
        jti = secrets.token_urlsafe(24)
        self._access_jtis.add(jti)
        payload = {"iss": self.base_url, "aud": resource,
                   "sub": "did:privy:faketestuser", "orgId": "org_fake",
                   "scope": scope, "exp": now + 900, "iat": now, "jti": jti}
        return ".".join((_b64(json.dumps({"alg": "none", "typ": "JWT"}).encode()),
                         _b64(json.dumps(payload).encode()), _b64(secrets.token_bytes(24))))

    def _issue_tokens(self, client_id: str, resource: str, scope: str,
                      family: str | None = None) -> dict:
        if family is None:
            family = secrets.token_urlsafe(24)
        self._latest_family = family
        refresh = secrets.token_urlsafe(32)
        self._refresh_tokens[refresh] = {"client_id": client_id, "resource": resource,
                                         "scope": scope, "family": family,
                                         "revoked": False, "rotated_at": None}
        return {"access_token": self._make_jwt(resource, scope), "token_type": "Bearer",
                "expires_in": 900, "refresh_token": refresh, "scope": scope}

    @staticmethod
    def _error(error: str, status: int = 400) -> JSONResponse:
        return JSONResponse({"error": error}, status_code=status)

    @staticmethod
    async def _form(request: Request) -> dict[str, str]:
        # Avoid python-multipart: these OAuth requests are URL-encoded, not multipart.
        data = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        return {key: values[0] for key, values in data.items()}

    def _install_auth_routes(self) -> None:
        @self.app.get("/.well-known/oauth-authorization-server")
        async def metadata() -> dict:
            return {"issuer": self.base_url,
                    **{name + "_endpoint": self.base_url + path for name, path in (
                        ("authorization", "/authorize"), ("token", "/token"),
                        ("registration", "/register"), ("revocation", "/revoke"))},
                    "scopes_supported": ["api-keys:write", "wallet:execute", "offline_access"],
                    "code_challenge_methods_supported": ["S256"],
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code", "refresh_token"]}

        @self.app.get("/client-info")
        async def client_info(client_id: str = ""):
            if not re.fullmatch(r"mcp_[A-Za-z0-9_-]{16,128}", client_id):
                return self._error("invalid_request")
            client = self._clients.get(client_id)
            if client is None:
                return self._error("not_found", 404)
            return {"client_id": client_id, "client_name": client["client_name"],
                    "redirect_uris_count": len(client["redirect_uris"])}

        @self.app.post("/register")
        async def register(request: Request):
            self._stats["registrations"] += 1
            data = await request.json()
            if (not isinstance(data, dict) or not isinstance(data.get("redirect_uris"), list)
                    or not all(isinstance(uri, str) for uri in data["redirect_uris"])):
                return self._error("invalid_request")
            client_id = "mcp_" + re.sub(r"[^A-Za-z0-9_-]", "_", secrets.token_urlsafe(24))
            client = {"client_id": client_id, "client_name": data.get("client_name", ""),
                      "redirect_uris": list(data["redirect_uris"])}
            self._clients[client_id] = client
            return JSONResponse({**client, "client_id_issued_at": int(time.time())}, status_code=201)

        @self.app.get("/authorize")
        async def authorize(request: Request):
            q = request.query_params
            client = self._clients.get(q.get("client_id", ""))
            if client is None:
                return self._error("invalid_client")
            redirect = q.get("redirect_uri", "")
            try:
                parsed = urlsplit(redirect)
                # RFC 8252: registered loopback URI omits the ephemeral port.
                compatible = any((parsed.scheme, parsed.hostname, parsed.path) ==
                                 (other.scheme, other.hostname, other.path)
                                 for other in map(urlsplit, client["redirect_uris"]))
            except ValueError:
                return self._error("invalid_request")
            if (not compatible or q.get("code_challenge_method") != "S256"
                    or q.get("response_type") != "code" or not q.get("code_challenge")
                    or not q.get("resource")):
                return self._error("invalid_request")
            params = {"state": q.get("state", "")}
            if q.get("_deny") == "1":
                params["error"] = "access_denied"
            else:
                scope = q.get("scope", "")
                if q.get("_no_wallet_consent") == "1":
                    scope = " ".join(s for s in scope.split() if s != "wallet:execute")
                code = secrets.token_urlsafe(32)
                self._codes[code] = {"client_id": client["client_id"], "redirect_uri": redirect,
                                     "code_challenge": q["code_challenge"], "scope": scope,
                                     "resource": q["resource"], "used": False,
                                     "created_at": time.time()}
                params["code"] = code
            query = parsed.query + ("&" if parsed.query else "") + urlencode(params)
            return RedirectResponse(urlunsplit(parsed._replace(query=query)), status_code=302)

        @self.app.post("/token")
        async def token(request: Request):
            self._stats["token_calls"] += 1
            if "dpop" in request.headers:
                return self._error("invalid_request")
            data = await self._form(request)
            if data.get("grant_type") == "authorization_code":
                code = self._codes.get(data.get("code", ""))
                if code is None or code["used"]:
                    return self._error("invalid_grant")
                code["used"] = True
                if not all(data.get(k) for k in ("code_verifier", "redirect_uri", "client_id", "resource")):
                    return self._error("invalid_request")
                # The token exchange binds the EXACT URI, including its port.
                challenge = _b64(hashlib.sha256(data["code_verifier"].encode()).digest())
                if (data["redirect_uri"] != code["redirect_uri"]
                        or data["client_id"] != code["client_id"]
                        or challenge != code["code_challenge"]):
                    return self._error("invalid_grant")
                if data["resource"] != code["resource"]:
                    return self._error("invalid_target")
                return self._issue_tokens(code["client_id"], code["resource"], code["scope"])
            if data.get("grant_type") == "refresh_token":
                self._stats["refreshes"] += 1
                if not data.get("refresh_token") or not data.get("resource"):
                    return self._error("invalid_request")
                old = self._refresh_tokens.get(data["refresh_token"])
                if old is None or old["revoked"] or old["family"] in self._revoked_families:
                    return self._error("invalid_grant")
                now = time.monotonic()
                # A fixed ten-second grace window tolerates concurrent refreshes.
                if old["rotated_at"] is not None and now - old["rotated_at"] > 10:
                    self._revoked_families.add(old["family"])
                    return self._error("invalid_grant")
                if data["resource"] != old["resource"]:
                    return self._error("invalid_target")
                if data.get("client_id", old["client_id"]) != old["client_id"]:
                    return self._error("invalid_grant")
                if old["rotated_at"] is None:
                    old["rotated_at"] = now
                return self._issue_tokens(old["client_id"], old["resource"], old["scope"], old["family"])
            return self._error("unsupported_grant_type")

        @self.app.post("/revoke")
        async def revoke(request: Request) -> dict:
            data = await self._form(request)
            old = self._refresh_tokens.get(data.get("token", ""))
            if old is not None:
                old["revoked"] = True
                self._revoked_families.add(old["family"])
            return {}

        @self.app.post("/_test/expire_access")
        async def expire_access() -> dict:
            self.expired_access_jtis.update(self._access_jtis)
            return {}

        @self.app.post("/_test/revoke_family")
        async def revoke_family() -> dict:
            if self._latest_family is not None:
                self._revoked_families.add(self._latest_family)
            return {}

        @self.app.get("/_test/stats")
        async def stats() -> dict:
            return self.stats()

    def _require_oauth(self, request: Request, required_scope: str) -> dict:
        try:
            authorization = request.headers.get("authorization", "").split()
            if len(authorization) != 2 or authorization[0].lower() != "bearer":
                raise ValueError("invalid bearer header")
            segments = authorization[1].split(".")
            if len(segments) != 3 or not all(segments):
                raise ValueError("invalid JWT shape")
            payload = json.loads(base64.urlsafe_b64decode(segments[1] + "=" * (-len(segments[1]) % 4)))
            if (not isinstance(payload, dict)
                    or not isinstance(payload.get("exp"), (int, float))
                    or not payload["exp"] > time.time()
                    or payload.get("aud") != self.base_url + "/api"
                    or not isinstance(payload.get("jti"), str)
                    or payload["jti"] in self.expired_access_jtis
                    or not isinstance(payload.get("scope"), str)
                    or not isinstance(payload.get("sub"), str)
                    or not isinstance(payload.get("orgId"), str)):
                raise ValueError("invalid JWT claims")
        except (ValueError, TypeError, UnicodeError) as exc:
            raise HTTPException(401, detail={"error": "invalid_token"}) from exc
        if required_scope not in payload["scope"].split():
            raise HTTPException(403, detail={"error": "insufficient_scope", "scope": required_scope})
        return payload

    # PARTE B1: desktop API keys and signer state.
    def _install_control_plane_routes(self) -> None:
        @self.app.exception_handler(HTTPException)
        async def oauth_error(request: Request, exc: HTTPException) -> JSONResponse:
            # OAuth errors use a top-level error, not FastAPI's detail envelope.
            return JSONResponse(exc.detail, status_code=exc.status_code)

        @self.app.post("/api/desktop/api-keys")
        async def mint(request: Request):
            self._stats["mints"] += 1
            payload = self._require_oauth(request, "api-keys:write")
            if self._mint_failure is not None:
                status, self._mint_failure = self._mint_failure, None
                return self._error("mint_failed", status)
            data = await request.json()
            device_id = data.get("device_id") if isinstance(data, dict) else None
            if not isinstance(device_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", device_id):
                return self._error("invalid_request")
            name = f"{data.get('name', '')} · {device_id[:8]}"
            for record in self._api_keys.values():
                if not record["revoked"] and record["org_id"] == payload["orgId"] and record["name"] == name:
                    record["revoked"] = True
            key = "mcfw_" + secrets.token_hex(32)
            scopes = ["mcp"]
            if "wallet:execute" in payload["scope"].split():
                scopes.append("wallet:execute")
            record = {"id": "key_" + secrets.token_urlsafe(16), "key": key,
                      "key_prefix": key[:12], "name": name, "device_id": device_id,
                      "org_id": payload["orgId"], "created_by": payload["sub"],
                      "scopes": scopes, "created_at": datetime.now(timezone.utc).isoformat(),
                      "revoked": False}
            self._api_keys[key] = record
            return JSONResponse({"id": record["id"], "key": key, "keyPrefix": key[:12],
                                 "name": name, "scopes": scopes, "createdAt": record["created_at"],
                                 "mcpUrl": self.base_url + "/mcp"}, status_code=201)

        @self.app.delete("/api/desktop/api-keys/{key_id}")
        async def delete_key(key_id: str, request: Request, device_id: str = ""):
            self._stats["deletes"] += 1
            payload = self._require_oauth(request, "api-keys:write")
            record = next((item for item in self._api_keys.values() if item["id"] == key_id), None)
            if (record is None or record["org_id"] != payload["orgId"]
                    or record["created_by"] != payload["sub"]
                    or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", device_id)
                    or not record["name"].endswith(f"· {device_id[:8]}")):
                return self._error("not_found", 404)
            # Retain the record: revocation mirrors the backend's soft-delete.
            record["revoked"] = True
            return {"success": True}

        @self.app.post("/api/wallet/signer-granted")
        async def grant_signer() -> dict:
            # Real route uses Privy auth, deliberately not the OAuth helper.
            if self._signer["address"] is None:
                self._signer["address"] = "0x1111111111111111111111111111111111111111"
            self._signer["granted_at"] = datetime.now(timezone.utc).isoformat()
            return {k: v for k, v in self.signer_state().items() if k != "linkedExternalAddress"}

        @self.app.get("/api/wallet/signer-granted")
        async def get_signer() -> dict:
            return self.signer_state()

        @self.app.delete("/api/wallet/signer-granted")
        async def delete_signer() -> dict:
            self._signer["granted_at"] = None
            return {"signerGranted": False}

        @self.app.post("/_test/grant_signer")
        async def test_grant_signer(request: Request) -> dict:
            data = await request.json()
            self._signer["address"] = data["address"]
            self._signer["granted_at"] = datetime.now(timezone.utc).isoformat()
            return self.signer_state()

        @self.app.post("/_test/revoke_signer")
        async def test_revoke_signer() -> dict:
            self._signer["granted_at"] = None
            return self.signer_state()

        @self.app.post("/_test/set_external_wallet")
        async def set_external_wallet(request: Request) -> dict:
            data = await request.json()
            self._signer["external"] = data["address"]
            return self.signer_state()

        @self.app.post("/_test/revoke_api_key")
        async def revoke_api_key(request: Request) -> dict:
            data = await request.json()
            revoked = False
            for record in self._api_keys.values():
                if (record["id"] == data.get("id") or
                        (isinstance(data.get("prefix"), str) and data["prefix"]
                         and record["key"].startswith(data["prefix"]))):
                    record["revoked"] = True
                    revoked = True
            return {"revoked": revoked}

        @self.app.post("/_test/mint_failure")
        async def mint_failure(request: Request):
            data = await request.json()
            status = data.get("status")
            if status is not None and (type(status) is not int or not 400 <= status <= 599):
                return self._error("invalid_request")
            self._mint_failure = status
            return {"status": status}


    @property
    def internal_secret(self) -> str:
        return self._internal_secret

    def gates(self) -> dict:
        return deepcopy(self._gates)

    def executions(self) -> dict:
        return deepcopy(self._executions)

    def privy_signature_public_key_pem(self) -> str:
        return self._privy_auth_pub.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")

    def _require_internal(self, request: Request) -> None:
        if not hmac.compare_digest(request.headers.get("X-Internal-Secret", "").encode(),
                                   self._internal_secret.encode()):
            raise HTTPException(401, detail={"error": "unauthorized"})

    @staticmethod
    def _gate_update(data: dict) -> dict:
        if (not all(isinstance(data.get(k), str) and data[k] for k in ("orgId", "userId"))
                or data.get("decision") not in ("allow", "warn", "block", "unknown", "pending")
                or any(type(data.get(k)) is not int for k in ("decidedAt", "expiresAt"))
                or (data.get("reason") is not None and not isinstance(data["reason"], str))):
            raise ValueError("invalid gate update")
        return {"decision": data["decision"], "reason": data.get("reason"),
                "decidedAt": data["decidedAt"],
                "expiresAt": min(data["expiresAt"], int(time.time() * 1000) + GATE_TTL_MS)}

    def _call_fake_privy(self, wallet_id, tx, chain_id, idempotency_key, reference_id):
        if self._privy_delay_ms > 0:
            return None
        url = f"{self.base_url}/v1/wallets/{wallet_id}/rpc"
        body = {"method": "eth_sendTransaction", "caip2": f"eip155:{chain_id}",
                "params": {"transaction": tx}, "reference_id": reference_id}
        headers = {"privy-app-id": "fake-app", "privy-idempotency-key": idempotency_key}
        payload = {"version": 1, "method": "POST", "url": url,
                   "body": body, "headers": headers.copy()}
        key = self._wrong_auth_key if self._corrupt_privy_signature else self._privy_auth_key
        signature = key.sign(_jcs(payload).encode("utf-8"), ec.ECDSA(hashes.SHA256()))
        headers.update({"Authorization": "Basic ZmFrZTpmYWtl",
                        "privy-authorization-signature": base64.b64encode(signature).decode("ascii")})
        with self.client_session() as session:
            return session.post(url, json=body, headers=headers, timeout=10)

    async def _submit_execution(self, record: dict) -> dict:
        if record.get("in_flight"):
            return {"ok": False, "code": "execution_pending"}
        record["in_flight"] = True
        try:
            # requests runs off the event loop so this server can handle its own RPC.
            response = await asyncio.to_thread(
                self._call_fake_privy, record["wallet_id"], record["tx"],
                record["chainId"], record["idempotency_key"], record["requestId"])
        except requests.RequestException:
            # Ambiguous transport failures retain the reservation and idempotency key.
            return {"ok": False, "code": "execution_pending"}
        finally:
            record["in_flight"] = False
        if response is None or response.status_code >= 500:
            return {"ok": False, "code": "execution_pending"}
        if 400 <= response.status_code < 500:
            record.update(status="failed", code="privy_rejected")
            daily = self._daily[record["daily_key"]]
            daily["spent_wei"] -= record["valueWei"]
            daily["tx_count"] -= 1
            return {"ok": False, "code": "privy_rejected"}
        record.update(status="submitted", txHash=response.json()["data"]["hash"])
        return {"ok": True, "txHash": record["txHash"], "chainId": record["chainId"]}

    def _record_gate(self, data: dict):
        try:
            update = self._gate_update(data)
            rid, payload = data["requestId"], data["payload"]
            if (not isinstance(rid, str) or not 8 <= len(rid) <= 128
                    or data.get("kind") not in ("swap", "approve", "withdraw")):
                raise ValueError("invalid gate")
            chain = payload["chainId"]
            if not (type(chain) is int or isinstance(chain, str) and re.fullmatch(r"[0-9]+", chain)):
                raise ValueError("invalid chain")
            for name in ("to", "from"):
                if not isinstance(payload[name], str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", payload[name]):
                    raise ValueError("invalid address")
            calldata, value = payload["callData"], payload["value"]
            if not isinstance(calldata, str) or not re.fullmatch(r"0x([0-9a-fA-F]{2})*", calldata):
                raise ValueError("invalid calldata")
            if type(value) is int:
                value = str(value)
            if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9]+|0x[0-9a-fA-F]+)", value):
                raise ValueError("invalid value")
            immutable = {"orgId": data["orgId"], "userId": data["userId"], "kind": data["kind"],
                         "chainId": int(chain), "to": payload["to"].lower(),
                         "from": payload["from"].lower(), "callData": calldata.lower(),
                         "valueWei": str(int(value, 16 if value.startswith("0x") else 10))}
        except (ValueError, TypeError, KeyError, AttributeError):
            return self._error("invalid_request")
        old = self._gates.get(rid)
        if old is not None:
            if old["consumed_at"] is not None or any(old[k] != v for k, v in immutable.items()):
                return self._error("gate_conflict", 409)
            old.update(update)
        else:
            self._gates[rid] = {"requestId": rid, **immutable, **update, "consumed_at": None}
        self._stats["gates"] += 1
        return Response(status_code=204)

    def _install_wallet_routes(self) -> None:
        @self.app.post("/api/internal/wallet/gate")
        async def gate(request: Request):
            self._require_internal(request)
            try:
                data = await request.json()
            except (ValueError, TypeError, KeyError, AttributeError):
                return self._error("invalid_request")
            return self._record_gate(data)

        @self.app.patch("/api/internal/wallet/gate/{requestId}")
        async def patch_gate(requestId: str, request: Request):
            self._require_internal(request)
            try:
                data = await request.json()
                update = self._gate_update(data)
            except (ValueError, TypeError, AttributeError):
                return self._error("invalid_request")
            old = self._gates.get(requestId)
            if (old is None or old["consumed_at"] is not None or old["kind"] != "swap"
                    or old["decision"] != "pending"
                    or any(old[k] != data[k] for k in ("orgId", "userId"))):
                return self._error("gate_not_found", 404)
            old.update(update)
            return Response(status_code=204)

        @self.app.post("/api/internal/wallet/execute")
        async def execute(request: Request):
            self._stats["executes"] += 1
            self._require_internal(request)
            try:
                data = await request.json()
            except ValueError:
                data = {}
            return await self._execute_gate(data)

        self._install_wallet_aux_routes()

    async def _execute_gate(self, data: dict) -> dict:
        if not isinstance(data, dict):
            data = {}
        def fail(code):
            return {"ok": False, "code": code}
        if not isinstance(data.get("userId"), str) or not data["userId"]:
            return fail("user_unresolved")
        if not self._signer or not self._signer.get("address") or not self._signer.get("granted_at"):
            return fail("signer_not_granted")
        rid = data.get("requestId")
        gate = self._gates.get(rid) if isinstance(rid, str) else None
        if gate is None:
            return fail("gate_not_found")
        if any(gate[k] != data.get(k) for k in ("orgId", "userId")):
            return fail("gate_owner_mismatch")
        record = self._executions.get(rid)
        if record is not None:
            if record["status"] == "submitted":
                return {"ok": True, "txHash": record["txHash"], "chainId": record["chainId"]}
            if record["status"] == "failed":
                return fail(record["code"])
            # Clearing the simulated delay resumes the SAME reserved execution
            # with the SAME idempotency key, without consuming/reserving again.
            if self._privy_delay_ms > 0:
                return fail("execution_pending")
            return await self._submit_execution(record)
        if gate["decision"] != "allow":
            return fail("gate_not_allowed")
        now = int(time.time() * 1000)
        if gate["expiresAt"] <= now:
            return fail("gate_expired")
        if gate["consumed_at"] is not None:
            return fail("gate_consumed")
        gate["consumed_at"] = now
        address, chain_id = gate["from"].lower(), gate["chainId"]
        if address != self._signer["address"].lower():
            return fail("wallet_mismatch")
        chain = FAKE_CHAINS.get(chain_id)
        if chain is None:
            return fail("chain_not_allowed")
        value = int(gate["valueWei"])
        if value > CAP_TX_WEI:
            return fail("cap_tx_exceeded")
        calldata, to = gate["callData"], gate["to"]
        selector = calldata[:10]
        if gate["kind"] == "approve":
            allowed = (selector == SELECTOR_APPROVE and value == 0 and len(calldata) == 138
                       and calldata[10:34] == "0" * 24
                       and "0x" + calldata[34:74] in (chain["v3"], chain["v2"]))
        elif gate["kind"] == "withdraw":
            allowed = (selector == SELECTOR_WETH_WITHDRAW and value == 0
                       and len(calldata) == 74 and to == chain["weth"])
        else:
            allowed = ((selector == SELECTOR_V3_EXACT_INPUT_SINGLE and to == chain["v3"])
                       or (selector in (SELECTOR_V2_BUY, SELECTOR_V2_SELL)
                           and chain["v2"] is not None and to == chain["v2"]))
        if not allowed:
            return fail("calldata_not_allowed")
        daily_key = (address, datetime.now(timezone.utc).date().isoformat())
        daily = self._daily.setdefault(daily_key, {"spent_wei": 0, "tx_count": 0})
        if daily["spent_wei"] + value > CAP_DAY_WEI or daily["tx_count"] >= MAX_TX_PER_DAY:
            return fail("cap_day_exceeded")
        daily["spent_wei"] += value
        daily["tx_count"] += 1
        record = {"requestId": rid, "status": "pending", "chainId": chain_id,
                  "daily_key": daily_key, "valueWei": value, "wallet_id": "fake-wallet",
                  "idempotency_key": rid,
                  "tx": {"chain_id": chain_id, "from": address, "to": to,
                         "data": calldata, "value": hex(value)}}
        self._executions[rid] = record
        return await self._submit_execution(record)

    def _install_wallet_aux_routes(self) -> None:
        @self.app.get("/api/internal/wallet/status")
        async def wallet_status(request: Request, userId: str = ""):
            self._require_internal(request)
            if not self._signer:
                return {"address": None, "signerGranted": False,
                        "signerGrantedAt": None, "linkedExternalAddress": None}
            return self.signer_state()

        @self.app.post("/v1/wallets/{wallet_id}/rpc")
        async def privy_rpc(wallet_id: str, request: Request):
            self._stats["privy_calls"] += 1
            headers = request.headers
            if not headers.get("authorization", "").startswith("Basic ") or not headers.get("privy-app-id"):
                return self._error("unauthorized", 401)
            idem = headers.get("privy-idempotency-key")
            if not idem:
                return self._error("invalid_request")
            try:
                body = await request.json()
            except ValueError:
                return self._error("invalid_request")
            try:
                signature = base64.b64decode(headers.get("privy-authorization-signature", ""), validate=True)
                payload = {"version": 1, "method": "POST", "url": str(request.url), "body": body,
                           "headers": {"privy-app-id": headers["privy-app-id"],
                                       "privy-idempotency-key": idem}}
                self._privy_auth_pub.verify(signature, _jcs(payload).encode("utf-8"), ec.ECDSA(hashes.SHA256()))
            except (InvalidSignature, ValueError, TypeError, UnicodeError):
                return self._error("invalid_authorization_signature", 401)
            if idem not in self._privy_idem:
                try:
                    tx = body["params"]["transaction"]
                    chain_id = tx["chain_id"]
                    chain = FAKE_CHAINS.get(chain_id) if type(chain_id) is int else None
                    value = tx["value"]
                    if (chain is None or body["method"] != "eth_sendTransaction"
                            or body["caip2"] != f"eip155:{chain_id}"
                            or tx["to"].lower() not in (chain["v3"], chain["v2"], chain["weth"])
                            or not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", value)
                            or int(value, 16) > CAP_TX_WEI
                            or tx["data"][:10].lower() not in (SELECTOR_APPROVE, SELECTOR_WETH_WITHDRAW,
                                SELECTOR_V3_EXACT_INPUT_SINGLE, SELECTOR_V2_BUY, SELECTOR_V2_SELL)):
                        raise ValueError("policy violation")
                except (KeyError, TypeError, ValueError, AttributeError):
                    return self._error("POLICY_VIOLATION")
                self._privy_idem[idem] = "0x" + secrets.token_hex(32)
            return {"data": {"hash": self._privy_idem[idem], "caip2": body.get("caip2"),
                             "transaction_id": str(uuid.uuid4())}}

        @self.app.post("/_test/privy_delay")
        async def privy_delay(request: Request):
            data = await request.json()
            if type(data.get("ms")) is not int or data["ms"] < 0:
                return self._error("invalid_request")
            self._privy_delay_ms = data["ms"]
            return {"ms": self._privy_delay_ms}

        @self.app.post("/_test/corrupt_privy_signature")
        async def corrupt_signature(request: Request):
            data = await request.json()
            if type(data.get("enabled")) is not bool:
                return self._error("invalid_request")
            self._corrupt_privy_signature = data["enabled"]
            return {"enabled": self._corrupt_privy_signature}

        @self.app.post("/_test/set_gate_decision")
        async def set_gate_decision(request: Request):
            data = await request.json()
            if "requestId" not in data:
                if data.get("decision") not in (None, "block", "warn", "unknown"):
                    return self._error("invalid_request")
                self._gate_decision_override = data.get("decision")
                return {}
            gate = self._gates.get(data.get("requestId"))
            if gate is None:
                return self._error("gate_not_found", 404)
            gate["decision"] = data["decision"]
            return {}

        @self.app.get("/_test/daily_spend")
        async def daily_spend(address: str = ""):
            key = (address.lower(), datetime.now(timezone.utc).date().isoformat())
            daily = self._daily.get(key, {"spent_wei": 0, "tx_count": 0})
            return {"spentWei": str(daily["spent_wei"]), "txCount": daily["tx_count"]}


    # Fake guardian-proxy: JSON responses on the Streamable HTTP endpoint.
    def _install_mcp_routes(self) -> None:
        names = ("guardian_pretrade_check", "guardian_pretrade_result",
                 "guardian_execute", "guardian_wallet_status", "crypto_get_price")

        @self.app.post("/_test/pretrade_pending")
        async def pretrade_pending(request: Request):
            data = await request.json()
            if type(data.get("enabled")) is not bool:
                return self._error("invalid_request")
            self._pretrade_pending = data["enabled"]
            return {"enabled": self._pretrade_pending}

        @self.app.post("/mcp")
        async def mcp(request: Request):
            authorization = request.headers.get("authorization", "").split()
            key = None
            if (len(authorization) == 2 and authorization[0].lower() == "bearer"
                    and authorization[1].startswith("mcfw_")):
                key = self._api_keys.get(authorization[1])
            if key is None or key["revoked"]:
                self._stats["mcp_401s"] += 1
                return self._error("unauthorized", 401)

            def error(code, message, rid=None):
                return JSONResponse({"jsonrpc": "2.0", "id": rid,
                                     "error": {"code": code, "message": message}})

            try:
                data = await request.json()
            except ValueError:
                return error(-32700, "Parse error")
            if not isinstance(data, dict):
                return error(-32600, "Invalid Request")
            rid, method = data.get("id"), data.get("method")
            headers = {}
            if method == "initialize":
                result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "fake-seraph", "version": "1.0.0"}}
                headers["Mcp-Session-Id"] = secrets.token_urlsafe(24)
            elif method == "notifications/initialized" and "id" not in data:
                return Response(status_code=202)
            elif method == "tools/list":
                result = {"tools": [{"name": name, "inputSchema": {
                    "type": "object", "properties": {}}} for name in names]}
            elif method == "tools/call":
                params = data.get("params", {})
                if not isinstance(params, dict):
                    return error(-32602, "Invalid params", rid)
                name = params.get("name")
                if name not in names:
                    return error(-32601, "Unknown tool", rid)
                if name == "guardian_execute" and "wallet:execute" not in key["scopes"]:
                    return self._error("insufficient_scope", 403)
                args = params.get("arguments", {})
                if not isinstance(args, dict):
                    return error(-32602, "Invalid arguments", rid)
                try:
                    output = await self._mcp_tool(name, args, key)
                except (ValueError, TypeError, KeyError, AttributeError):
                    return error(-32602, "Invalid arguments", rid)
                result = {"content": [{"type": "text", "text": json.dumps(output)}],
                          "isError": False}
            else:
                return error(-32601, "Unknown method", rid)
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result}, headers=headers)

    async def _mcp_tool(self, name: str, args: dict, key: dict) -> dict:
        if name == "crypto_get_price":
            return {"asset": args.get("asset", "ETH"), "priceUsd": 3000.0}
        if name == "guardian_wallet_status":
            return {"ok": True, **self.signer_state(),
                    "chains": [{"chainId": chain, "nativeBalanceWei": "1000000000000000000"}
                               for chain in sorted(FAKE_CHAINS)],
                    "linkedExternalChains": sorted(FAKE_CHAINS) if self._signer["external"] else []}
        if name == "guardian_execute":
            self._stats["executes"] += 1
            result = await self._execute_gate({"requestId": args.get("requestId"),
                                              "orgId": "org_fake", "userId": key["created_by"]})
            if not result["ok"]:
                return {"ok": False, "error": result["code"],
                        "message": result["code"].replace("_", " ")}
            return result
        if name == "guardian_pretrade_result":
            rid = args.get("requestId")
            gate = self._gates.get(rid)
            if (gate is None or gate["expiresAt"] <= int(time.time() * 1000)
                    or gate["userId"] != key["created_by"] or gate["orgId"] != "org_fake"):
                return {"error": "unknown_or_expired_request"}
            if gate["decision"] == "pending":
                gate.update(decision="allow", reason="upstream_verdict",
                            decidedAt=int(time.time() * 1000))
            return {"status": "complete", "decision": gate["decision"], "requestId": rid}

        chain = FAKE_CHAINS.get(int(args["chainId"]), {})
        calldata, to = args["callData"].lower(), args["to"].lower()
        value = str(args.get("value", "0"))
        zero = int(value, 16 if value.startswith("0x") else 10) == 0
        kind = "swap"
        if (calldata[:10] == SELECTOR_APPROVE and zero and len(calldata) == 138
                and calldata[10:34] == "0" * 24
                and "0x" + calldata[34:74] in (chain.get("v3"), chain.get("v2"))):
            kind = "approve"
        elif (calldata[:10] == SELECTOR_WETH_WITHDRAW and zero and len(calldata) == 74
              and to == chain.get("weth")):
            kind = "withdraw"
        decision = "allow"
        if kind == "swap":
            decision = self._gate_decision_override or ("pending" if self._pretrade_pending else "allow")
        rid, now = "gate_" + str(uuid.uuid4()), int(time.time() * 1000)
        response = self._record_gate({
            "requestId": rid, "orgId": "org_fake", "userId": key["created_by"], "kind": kind,
            "decision": decision, "reason": "upstream_verdict" if kind == "swap" else "non_swap_allowlisted",
            "decidedAt": now, "expiresAt": now + GATE_TTL_MS,
            "payload": {"chainId": args["chainId"], "to": to, "callData": calldata,
                        "value": value, "from": args.get("from", self._signer["address"])}})
        if response.status_code != 204:
            raise ValueError("invalid gate")
        result = {"decision": decision, "requestId": rid, "reasons": [], "gateRecorded": True}
        if decision == "pending":
            result.update(status="pending", retryAfterMs=50)
        return result
