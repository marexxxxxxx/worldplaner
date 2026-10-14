"""Transport-Sicherheitsfilter: Auth (F.1) und Rate Limit (F.1).

Reine ASGI-Middleware, daher ohne echten MCP-Server testbar.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.main import CloudflareAccessMiddleware, RateLimitMiddleware


def _inner_app() -> Starlette:
    async def endpoint(_request):  # type: ignore[no-untyped-def]
        return PlainTextResponse("ok")

    return Starlette(routes=[Route("/mcp", endpoint, methods=["GET", "POST"])])


class StubVerifier:
    def __init__(self, valid: bool) -> None:
        self.valid = valid
        self.seen: list[str] = []

    async def verify(self, token: str) -> bool:
        self.seen.append(token)
        return self.valid


def _error(response) -> dict:  # type: ignore[no-untyped-def]
    return response.json()["error"]


# --- Auth -----------------------------------------------------------------


def test_missing_assertion_is_unauthorized():
    verifier = StubVerifier(valid=True)
    app = CloudflareAccessMiddleware(_inner_app(), verifier=verifier, enabled=True)
    response = TestClient(app).get("/mcp")
    assert response.status_code == 401
    assert _error(response)["code"] == "UNAUTHORIZED"
    assert verifier.seen == []


def test_invalid_assertion_is_unauthorized():
    verifier = StubVerifier(valid=False)
    app = CloudflareAccessMiddleware(_inner_app(), verifier=verifier, enabled=True)
    response = TestClient(app).get("/mcp", headers={"Cf-Access-Jwt-Assertion": "bad"})
    assert response.status_code == 401
    assert _error(response)["code"] == "UNAUTHORIZED"
    assert verifier.seen == ["bad"]


def test_valid_assertion_passes():
    verifier = StubVerifier(valid=True)
    app = CloudflareAccessMiddleware(_inner_app(), verifier=verifier, enabled=True)
    response = TestClient(app).get("/mcp", headers={"Cf-Access-Jwt-Assertion": "good"})
    assert response.status_code == 200
    assert response.text == "ok"


def test_disabled_auth_delegates_to_edge():
    app = CloudflareAccessMiddleware(_inner_app(), verifier=None, enabled=False)
    assert TestClient(app).get("/mcp").status_code == 200


# --- Rate Limit -----------------------------------------------------------


def test_rate_limit_blocks_after_budget():
    app = RateLimitMiddleware(_inner_app(), limit_per_minute=2)
    client = TestClient(app)
    assert client.get("/mcp").status_code == 200
    assert client.get("/mcp").status_code == 200
    blocked = client.get("/mcp")
    assert blocked.status_code == 429
    assert _error(blocked)["code"] == "RATE_LIMITED"
    assert _error(blocked)["retryable"] is True
    assert "Retry-After" in blocked.headers
    assert _error(blocked)["retry_after"] >= 1


def test_rate_limit_is_per_identity():
    app = RateLimitMiddleware(_inner_app(), limit_per_minute=1)
    client = TestClient(app)
    assert client.get("/mcp", headers={"Cf-Access-Client-Id": "a"}).status_code == 200
    assert client.get("/mcp", headers={"Cf-Access-Client-Id": "a"}).status_code == 429
    assert client.get("/mcp", headers={"Cf-Access-Client-Id": "b"}).status_code == 200
