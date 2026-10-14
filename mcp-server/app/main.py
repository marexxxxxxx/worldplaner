"""worldplaner MCP-Server — Streamable-HTTP-Transport, Auth, Rate Limit (F.1).

Einziger Endpunkt: ``POST /mcp`` (und ``GET /mcp`` fuer den Server-Stream).
Der Server bindet im Container auf ``MCP_HOST``; die Beschraenkung auf
``127.0.0.1`` passiert ueber das Compose-Portmapping, die Exposition ueber
Cloudflare Tunnel + Access.

Sicherheitsmodell (Default deny):
* DNS-Rebinding-Schutz mit expliziter Host-/Origin-Allowlist (F.1).
* Cloudflare-Access-Assertion wird geprueft, sobald ``CF_ACCESS_AUD`` und
  ``CF_ACCESS_TEAM_DOMAIN`` gesetzt sind (``MCP_AUTH_MODE=auto``), per
  ``MCP_AUTH_MODE=enforce`` erzwungen (fehlende Konfiguration = Startfehler)
  oder per ``edge`` ausdruecklich an den Edge delegiert.
* Token-Bucket-Rate-Limit pro Identitaet (``MCP_RATE_LIMIT_PER_MINUTE``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.routing import Route

from .backend_client import BackendClient
from .config import Settings, get_settings
from .display import DisplayController
from .tools import Tools, build_tools

logger = logging.getLogger("worldplaner.mcp")

SERVER_NAME = "worldplaner-mcp"
SERVER_VERSION = "0.1.0"
MCP_PATH = "/mcp"

_INSTRUCTIONS = (
    "MCP-Server fuer das Kiosk-Dashboard (worldplaner). Task-CRUD wird an die "
    "Backend-REST-API delegiert (kein direkter Datenbankzugriff). "
    "Display-Steuerung erfolgt ueber xset/xrandr."
)


# --- Cloudflare Access -----------------------------------------------------


class CloudflareAccessVerifier:
    """Prueft ein ``Cf-Access-Jwt-Assertion`` gegen Cloudflare Access (F.1)."""

    def __init__(self, team_domain: str, audience: str, cache_ttl: float = 3600.0) -> None:
        self._team = team_domain.strip().strip("/")
        self._audience = audience.strip()
        self._cache_ttl = cache_ttl
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0

    @property
    def jwks_url(self) -> str:
        return f"https://{self._team}/cdn-cgi/access/certs"

    async def _signing_keys(self) -> dict[str, Any]:
        if self._keys and (time.monotonic() - self._fetched_at) < self._cache_ttl:
            return self._keys
        import httpx
        import jwt as pyjwt

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(self.jwks_url)
            response.raise_for_status()
            jwks = response.json()
        keys: dict[str, Any] = {}
        for jwk in jwks.get("keys", []):
            kid = jwk.get("kid")
            if kid:
                keys[kid] = pyjwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
        self._keys = keys
        self._fetched_at = time.monotonic()
        return keys

    async def verify(self, token: str) -> bool:
        try:
            import jwt as pyjwt
        except ImportError:  # pragma: no cover — PyJWT ist deklarierte Abhaengigkeit
            logger.error("PyJWT fehlt; Cloudflare-Access-Pruefung nicht moeglich")
            return False
        try:
            header = pyjwt.get_unverified_header(token)
            keys = await self._signing_keys()
            key = keys.get(header.get("kid"))
            if key is None:
                return False
            pyjwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=f"https://{self._team}",
            )
        except Exception:  # noqa: BLE001 — jede Abweichung ist ein Nein
            return False
        return True


async def _send_json(
    send: Any, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None
) -> None:
    raw = [(b"content-type", b"application/json")]
    for key, value in (headers or {}).items():
        raw.append((key.lower().encode(), str(value).encode()))
    body = json.dumps(payload).encode()
    await send({"type": "http.response.start", "status": status, "headers": raw})
    await send({"type": "http.response.body", "body": body})


class CloudflareAccessMiddleware:
    """Reine ASGI-Middleware: ohne gueltiges Assertion keine Session (F.1)."""

    def __init__(self, app: Any, *, verifier: CloudflareAccessVerifier | None, enabled: bool) -> None:
        self.app = app
        self._verifier = verifier
        self._enabled = enabled

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if (
            scope.get("type") != "http"
            or not self._enabled
            or scope.get("path") != MCP_PATH
        ):
            await self.app(scope, receive, send)
            return
        token = Headers(scope=scope).get("cf-access-jwt-assertion")
        if not token or self._verifier is None or not await self._verifier.verify(token):
            await _send_json(
                send,
                401,
                {
                    "ok": False,
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "Cloudflare Access Assertion fehlt oder ist ungueltig",
                        "retryable": False,
                    },
                },
            )
            return
        await self.app(scope, receive, send)


# --- Rate Limiting ---------------------------------------------------------


class _TokenBucket:
    def __init__(self, rate_per_minute: int) -> None:
        self._capacity = float(rate_per_minute)
        self._tokens = float(rate_per_minute)
        self._rate = rate_per_minute / 60.0
        self._updated = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
        self._updated = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    def retry_after(self) -> int:
        if self._rate <= 0:
            return 60
        deficit = 1.0 - self._tokens
        return max(1, int(deficit / self._rate + 0.999))


class RateLimitMiddleware:
    """Token-Bucket pro Service-Token/Client; Ueberschreitung -> RATE_LIMITED (F.1)."""

    def __init__(self, app: Any, *, limit_per_minute: int) -> None:
        self.app = app
        self._limit = limit_per_minute
        self._buckets: dict[str, _TokenBucket] = {}

    def _identity(self, scope: dict[str, Any]) -> str:
        headers = Headers(scope=scope)
        token = headers.get("cf-access-jwt-assertion") or headers.get("cf-access-client-id")
        if token:
            return hashlib.sha256(token.encode()).hexdigest()
        client = scope.get("client") or ("unknown", 0)
        return f"ip:{client[0]}"

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("path") != MCP_PATH or self._limit <= 0:
            await self.app(scope, receive, send)
            return
        identity = self._identity(scope)
        bucket = self._buckets.get(identity)
        if bucket is None:
            bucket = _TokenBucket(self._limit)
            self._buckets[identity] = bucket
        if not bucket.allow():
            retry_after = bucket.retry_after()
            await _send_json(
                send,
                429,
                {
                    "ok": False,
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Rate limit ueberschritten",
                        "retryable": True,
                        "retry_after": retry_after,
                    },
                },
                headers={"Retry-After": str(retry_after)},
            )
            return
        await self.app(scope, receive, send)


# --- App-Aufbau ------------------------------------------------------------


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _transport_security(settings: Settings) -> TransportSecuritySettings:
    hosts = _split_csv(settings.mcp_allowed_hosts) or [
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
    ]
    origins = _split_csv(settings.mcp_allowed_origins) or [
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
    ]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def _install_lifespan(starlette_app: Any, on_shutdown: Any) -> None:
    original = starlette_app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: Any):  # type: ignore[no-untyped-def]
        async with original(app) as state:
            try:
                yield state
            finally:
                await on_shutdown()

    starlette_app.router.lifespan_context = lifespan


def create_app(settings: Settings | None = None) -> Any:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO)

    backend = BackendClient(settings.backend_url, settings.backend_timeout_seconds)
    display = DisplayController(
        settings.display, settings.display_output, settings.display_timeout_seconds
    )
    tools = Tools(backend, display)
    tool_defs = build_tools()
    auth_enabled = settings.auth_enabled  # wirft bei enforce ohne Konfiguration

    async def on_list_tools(
        _context: Any, _params: Any
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tool_defs)

    async def on_call_tool(
        _context: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        result = await tools.dispatch(params.name, params.arguments or {})
        return types.CallToolResult(
            content=[
                types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))
            ]
        )

    server = Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions=_INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )

    async def healthz(_request: Any) -> JSONResponse:
        return JSONResponse({"status": "ok", "server": SERVER_NAME})

    starlette_app = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=False,
        stateless_http=False,
        transport_security=_transport_security(settings),
        custom_starlette_routes=[Route("/healthz", healthz, methods=["GET"])],
    )
    _install_lifespan(starlette_app, backend.aclose)

    verifier = (
        CloudflareAccessVerifier(settings.cf_access_team_domain, settings.cf_access_aud)
        if auth_enabled
        else None
    )

    if auth_enabled:
        logger.info("Cloudflare-Access-Pruefung aktiv (AUD gesetzt)")
    else:
        logger.warning(
            "Cloudflare-Access-Pruefung INAKTIV (MCP_AUTH_MODE=%s). "
            "Der Server darf nur hinter dem Edge auf 127.0.0.1 erreichbar sein.",
            settings.auth_mode,
        )

    # Reihenfolge: Auth aussen, Rate Limit innen.
    app: Any = RateLimitMiddleware(
        starlette_app, limit_per_minute=settings.mcp_rate_limit_per_minute
    )
    app = CloudflareAccessMiddleware(app, verifier=verifier, enabled=auth_enabled)
    return app


app = create_app()
