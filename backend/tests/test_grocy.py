"""Grocy-Adapter `GET /api/grocy/stock` (spec-phase2 D/WP-P2-2).

Die Grocy-API wird auf HTTP-Ebene gemockt (`app.services.grocy.httpx.get`), so
dass URL, Auth-Header, Normalisierung und `api_cache`-Pufferung mitgeprueft
werden. Der Router ist in main.py noch nicht registriert (das macht der
Integrationsschritt, Plan 10.2), darum wird hier eine lokale Test-App gebaut.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import get_settings
from app.main import _register_error_handlers
from app.routers import grocy
from app.services import grocy as grocy_service

CACHE_KEY = "grocy:stock"
WIDGET_DIR = Path(__file__).resolve().parents[2] / "widgets" / "grocy-stock"

# Realistische Grocy-Antwort (`GET /api/stock`, Grocy >= 3.3): je Produkt
# `product_id`, `amount`, `best_before_date` und verschachteltes `product`.
GROCY_STOCK = [
    {
        "product_id": 1,
        "amount": 2.0,
        "best_before_date": "2099-01-01",
        "product": {"id": 1, "name": "Milch", "qu_name": "l"},
    },
    {
        "product_id": 2,
        "amount": 0.5,
        "best_before_date": "2000-01-01",
        "stock_unit_name": "kg",
        "product": {"id": 2, "name": "Mehl"},
    },
]

EXPECTED_PRODUCTS = [
    {
        "product_id": 1,
        "name": "Milch",
        "amount": 2.0,
        "unit": "l",
        "best_before_date": "2099-01-01",
        "is_expired": False,
    },
    {
        "product_id": 2,
        "name": "Mehl",
        "amount": 0.5,
        "unit": "kg",
        "best_before_date": "2000-01-01",
        "is_expired": True,
    },
]


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture
def grocy_env(monkeypatch):
    monkeypatch.setenv("GROCY_URL", "https://grocy.example")
    monkeypatch.setenv("GROCY_API_KEY", "test-key")
    monkeypatch.setenv("GROCY_CACHE_TTL_SECONDS", "600")
    monkeypatch.setenv("GROCY_TIMEOUT_SECONDS", "10")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def grocy_client(db_path, grocy_env):
    db_module.init_db()
    app = FastAPI()
    _register_error_handlers(app)
    app.include_router(grocy.router, prefix="/api")
    with TestClient(app) as client:
        yield client


def test_stock_normalizes_products_and_uses_cache(grocy_client, monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append({"url": url, "headers": headers, "timeout": timeout})
        return FakeResponse(GROCY_STOCK)

    monkeypatch.setattr(grocy_service.httpx, "get", fake_get)

    first = grocy_client.get("/api/grocy/stock")
    assert first.status_code == 200
    body = first.json()
    assert body["products"] == EXPECTED_PRODUCTS
    assert isinstance(body["fetched_at"], str) and body["fetched_at"].endswith("Z")

    # Kriterium 1: Quell-Endpunkt + Auth-Header (gemockte Grocy-API).
    assert calls[0]["url"] == "https://grocy.example/api/stock"
    assert calls[0]["headers"] == {"GROCY-API-KEY": "test-key"}

    second = grocy_client.get("/api/grocy/stock")
    assert second.json() == body
    assert len(calls) == 1  # zweiter Aufruf kommt aus dem Cache (TTL 600 s)


def test_empty_result_is_not_an_error(grocy_client, monkeypatch):
    monkeypatch.setattr(
        grocy_service.httpx, "get", lambda *a, **k: FakeResponse([])
    )
    response = grocy_client.get("/api/grocy/stock")
    assert response.status_code == 200  # Kriterium 4: leer -> kein Fehler
    body = response.json()
    assert body["products"] == []
    assert "error_code" not in body


def test_unreachable_source_without_cache(grocy_client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(grocy_service.httpx, "get", boom)
    response = grocy_client.get("/api/grocy/stock")
    assert response.status_code == 200  # Kriterium 3: 200 statt 5xx
    assert response.json() == {"products": [], "error_code": "GROCY_UNAVAILABLE"}


def test_falls_back_to_expired_cache_on_provider_error(grocy_client, conn, monkeypatch):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    cached = {"products": EXPECTED_PRODUCTS, "fetched_at": past}

    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    # erst fuellen (Fake liefert Daten), dann den Eintrag ablaufen lassen und den
    # Provider-Fehler erzwingen -> der letzte Cache-Wert wird geliefert.
    monkeypatch.setattr(
        grocy_service.httpx, "get", lambda *a, **k: FakeResponse(GROCY_STOCK)
    )
    assert grocy_client.get("/api/grocy/stock").json()["products"] == EXPECTED_PRODUCTS

    conn.execute(
        "UPDATE api_cache SET payload_json = ?, fetched_at = ?, expires_at = ? WHERE key = ?",
        (json.dumps(cached), past, past, CACHE_KEY),
    )
    monkeypatch.setattr(grocy_service.httpx, "get", boom)
    assert grocy_client.get("/api/grocy/stock").json() == cached


def test_missing_best_before_date_is_not_expired(grocy_client, monkeypatch):
    stock = [{"product_id": 7, "amount": "1", "product": {"id": 7, "name": "Reis"}}]
    monkeypatch.setattr(
        grocy_service.httpx, "get", lambda *a, **k: FakeResponse(stock)
    )
    products = grocy_client.get("/api/grocy/stock").json()["products"]
    assert products == [
        {
            "product_id": 7,
            "name": "Reis",
            "amount": 1.0,
            "unit": "",
            "best_before_date": None,
            "is_expired": False,
        }
    ]


def test_stock_is_gated_by_grocy_permission(grocy_client, conn, monkeypatch):
    """WP-P2-6 (E.4): der neue Endpunkt ist serverseitig gegen `grocy` gegatet."""
    monkeypatch.setattr(grocy_service.httpx, "get", lambda *a, **k: FakeResponse([]))
    conn.execute(
        "INSERT INTO widgets (id, name, version, category, manifest_json, config_json) "
        "VALUES (?, ?, '1.0.0', 'general', ?, '{}')",
        (
            "w-no-grocy",
            "w-no-grocy",
            json.dumps({"id": "w-no-grocy", "permissions": []}),
        ),
    )
    conn.execute(
        "INSERT INTO widgets (id, name, version, category, manifest_json, config_json) "
        "VALUES (?, ?, '1.0.0', 'general', ?, '{}')",
        (
            "w-grocy",
            "w-grocy",
            json.dumps({"id": "w-grocy", "permissions": ["grocy"]}),
        ),
    )

    denied = grocy_client.get("/api/grocy/stock", headers={"X-Widget-Id": "w-no-grocy"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN"

    allowed = grocy_client.get("/api/grocy/stock", headers={"X-Widget-Id": "w-grocy"})
    assert allowed.status_code == 200

    # Ohne Header (App-Shell/MCP) bleibt der Aufruf erlaubt.
    assert grocy_client.get("/api/grocy/stock").status_code == 200


def test_widget_grocy_stock_is_complete():
    """Kriterium 2: Das Widget existiert vollstaendig und deklariert `grocy`."""
    manifest = json.loads((WIDGET_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "grocy-stock"
    assert manifest["permissions"] == ["grocy"]
    assert manifest["category"] in {"weather", "health", "productivity", "general"}
    assert all(
        isinstance(manifest["default_size"][axis], int) and manifest["default_size"][axis] >= 1
        for axis in ("w", "h")
    )
    for relative in (
        "frontend/widget.js",
        "frontend/style.css",
        "frontend/icon.svg",
        "README.md",
    ):
        assert (WIDGET_DIR / relative).is_file(), f"{relative} fehlt"
