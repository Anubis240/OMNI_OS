"""LOCAL test fake: no real credentials or production secrets live here.

HTTPS uses a disposable self-signed certificate trusted via client_session().
SeraphAuth._valid_metadata requires HTTPS and same-origin endpoints, including
DEFAULT_ENDPOINTS before discovery performs any request. HTTP cannot use the
fallback. Patching that validation was rejected: it disables the very defense
these integration tests should exercise.

expire_access records all issued JWT jtis in expired_access_jtis; Part B must
consult that set in addition to JWT exp. Newly issued tokens remain valid.
"""

import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
import requests
import uvicorn


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
        self._install_auth_routes()
        self._install_control_plane_routes()

    @property
    def base_url(self) -> str:
        return self._base_url

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


# PARTE B2: wallet executor + fake Privy.
# PARTE C: fake /mcp.
