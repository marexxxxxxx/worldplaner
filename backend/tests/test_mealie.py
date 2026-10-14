"""Mealie-Adapter `GET /api/mealie/mealplan` (spec-phase2 D/WP-P2-3).

Die Mealie-API wird auf HTTP-Ebene gemockt (`app.services.mealie.httpx.get`), so
dass Quell-URL, Auth-Header, Normalisierung, Zeitfenster und
`api_cache`-Pufferung mitgeprueft werden. Der Router ist in `main.py` noch nicht
registriert (das macht der Integrationsschritt, Plan 10.2), darum wird hier eine
lokale Test-App gebaut — mit den Fehlerhandlern aus `create_app`, damit
ungueltige `days` als `400 VALIDATION_ERROR` ankommen (Kriterium 4).
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
from app.routers import mealie
from app.services import mealie as mealie_service

WIDGET_DIR = Path(__file__).resolve().parents[2] / "widgets" / "mealie-mealplan"

TODAY = datetime.now(timezone.utc)
DAY = (TODAY + timedelta(days=1)).strftime("%Y-%m-%d")
DAY_2 = (TODAY + timedelta(days=2)).strftime("%Y-%m-%d")
TODAY_S = TODAY.strftime("%Y-%m-%d")
FAR = (TODAY + timedelta(days=30)).strftime("%Y-%m-%d")
CACHE_KEY = f"mealie:mealplan:{TODAY_S}"

# Realistische Mealie-v2-Antwort (`GET /api/groups/mealplans/this-week`): je
# Eintrag `date`, `entry_type`, verschachteltes `recipe` und `servings`.
MEALIE_WEEK = [
    {"date": TODAY_S, "entry_type": "dinner", "recipe": {"id": "r1", "name": "Bolognese"}, "servings": 2},
    {"date": TODAY_S, "entry_type": "breakfast", "title": "Overnight Oats", "servings": 1},
    {"date": DAY, "meal_type": "lunch", "recipe": None, "recipe_id": "r2", "servings": "4"},
    {"date": DAY_2, "entry_type": "side", "recipe": {"id": "r3", "name": "Salat"}},
    {"date": FAR, "entry_type": "dinner", "recipe": {"id": "r9", "name": "Fern"}},
    {"entry_type": "dinner", "recipe": {"id": "rX", "name": "Ohne Datum"}},
]

# Erwartete Normalform fuer `days=7`: nach (date, Mahlzeitentyp, Name) sortiert;
# `FAR` liegt ausserhalb des Fensters, der Eintrag ohne Datum wird verworfen.
EXPECTED_MEALS = [
    {"date": TODAY_S, "meal_type": "breakfast", "recipe_id": "", "recipe_name": "Overnight Oats", "servings": 1},
    {"date": TODAY_S, "meal_type": "dinner", "recipe_id": "r1", "recipe_name": "Bolognese", "servings": 2},
    {"date": DAY, "meal_type": "lunch", "recipe_id": "r2", "recipe_name": "", "servings": 4},
    {"date": DAY_2, "meal_type": "side", "recipe_id": "r3", "recipe_name": "Salat", "servings": 1},
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
def mealie_env(monkeypatch):
    monkeypatch.setenv("MEALIE_URL", "https://mealie.example")
    monkeypatch.setenv("MEALIE_API_TOKEN", "test-token")
    monkeypatch.setenv("MEALIE_CACHE_TTL_SECONDS", "600")
    monkeypatch.setenv("MEALIE_DAYS_AHEAD", "7")
    monkeypatch.setenv("MEALIE_TIMEOUT_SECONDS", "10")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def mealie_client(db_path, mealie_env):
    # Lokale Test-App mit den Fehlerhandlern aus `main.py` (damit `days`
    # ungueltig als `400 VALIDATION_ERROR` ankommt, Kriterium 4). Der Router
    # wird hier montiert, weil `main.py` erst der Integrationsschritt anfasst.
    db_module.init_db()
    app = FastAPI()
    _register_error_handlers(app)
    app.include_router(mealie.router, prefix="/api")
    with TestClient(app) as client:
        yield client


# --- Kriterium 1: 200 mit `meals`-Schema + Quell-Endpunkt/Auth ---------------

def test_mealplan_returns_normalized_meals_and_uses_cache(mealie_client, monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append({"url": url, "headers": headers, "timeout": timeout})
        return FakeResponse(MEALIE_WEEK)

    monkeypatch.setattr(mealie_service.httpx, "get", fake_get)

    first = mealie_client.get("/api/mealie/mealplan", params={"days": 7})
    assert first.status_code == 200
    body = first.json()
    assert body["meals"] == EXPECTED_MEALS
    assert isinstance(body["fetched_at"], str) and body["fetched_at"].endswith("Z")
    # Schema {date, meal_type, recipe_id, recipe_name, servings} je Mahlzeit.
    for meal in body["meals"]:
        assert set(meal) == {"date", "meal_type", "recipe_id", "recipe_name", "servings"}
        assert isinstance(meal["servings"], int)

    # Quell-Endpunkt + Bearer-Token (gemockte Mealie-API).
    assert calls[0]["url"] == "https://mealie.example/api/groups/mealplans/this-week"
    assert calls[0]["headers"] == {"Authorization": "Bearer test-token"}
    assert calls[0]["timeout"] == 10

    second = mealie_client.get("/api/mealie/mealplan", params={"days": 7})
    assert second.json() == body
    assert len(calls) == 1  # zweiter Aufruf kommt aus dem Cache (TTL 600 s)


def test_default_days_comes_from_env(mealie_client, monkeypatch):
    monkeypatch.setattr(
        mealie_service.httpx, "get", lambda *a, **k: FakeResponse(MEALIE_WEEK)
    )
    # Ohne `days` greift MEALIE_DAYS_AHEAD (7) -> identisch zu ?days=7.
    assert mealie_client.get("/api/mealie/mealplan").json()["meals"] == EXPECTED_MEALS


def test_days_filters_window(mealie_client, monkeypatch):
    monkeypatch.setattr(
        mealie_service.httpx, "get", lambda *a, **k: FakeResponse(MEALIE_WEEK)
    )
    body = mealie_client.get("/api/mealie/mealplan", params={"days": 1}).json()
    assert body["meals"] == EXPECTED_MEALS[:2]  # nur heute


# --- Kriterium 3: nicht erreichbare Quelle ohne Cache -> 200 + error_code ----

def test_unreachable_source_without_cache(mealie_client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(mealie_service.httpx, "get", boom)
    response = mealie_client.get("/api/mealie/mealplan", params={"days": 7})
    assert response.status_code == 200  # 200 statt 5xx
    assert response.json() == {"meals": [], "error_code": "MEALIE_UNAVAILABLE"}


def test_falls_back_to_expired_cache_on_provider_error(mealie_client, conn, monkeypatch):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    monkeypatch.setattr(
        mealie_service.httpx, "get", lambda *a, **k: FakeResponse(MEALIE_WEEK)
    )
    first = mealie_client.get("/api/mealie/mealplan", params={"days": 7}).json()
    assert first["meals"] == EXPECTED_MEALS

    conn.execute(
        "UPDATE api_cache SET expires_at = ? WHERE key = ?", (past, CACHE_KEY)
    )

    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(mealie_service.httpx, "get", boom)
    assert mealie_client.get("/api/mealie/mealplan", params={"days": 7}).json() == first


# --- Kriterium 4: `days` ungueltig -> 400 VALIDATION_ERROR -------------------

def test_days_out_of_range_is_validation_error(mealie_client):
    for invalid in (0, 8, -1):
        response = mealie_client.get("/api/mealie/mealplan", params={"days": invalid})
        assert response.status_code == 400, invalid
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- Normalisierung (Kriterium 1, Details) -----------------------------------

def test_normalize_accepts_items_object_and_drops_dateless():
    payload = {"items": [{"date": TODAY_S, "entry_type": "dinner", "recipe": {"id": "a"}}]}
    assert mealie_service.normalize_mealplan(payload) == [
        {"date": TODAY_S, "meal_type": "dinner", "recipe_id": "a", "recipe_name": "", "servings": 1}
    ]
    assert mealie_service.normalize_mealplan("nonsense") == []
    assert mealie_service.days_ahead({"days_ahead": 99}) == 7
    assert mealie_service.days_ahead({"days_ahead": 0}) == 1


# --- Serverseitiges Permission-Gate (E.4/WP-P2-6) ---------------------------

def _insert_widget(conn, widget_id, permissions):
    conn.execute(
        "INSERT INTO widgets (id, name, version, manifest_json) VALUES (?, ?, ?, ?)",
        (widget_id, widget_id, "1.0.0", json.dumps({"permissions": permissions})),
    )


def test_permission_gate_allows_and_denies(mealie_client, conn, monkeypatch):
    monkeypatch.setattr(
        mealie_service.httpx, "get", lambda *a, **k: FakeResponse([])
    )
    _insert_widget(conn, "mealie-mealplan", ["mealie"])
    _insert_widget(conn, "fremd-widget", ["weather"])

    assert mealie_client.get(
        "/api/mealie/mealplan", headers={"X-Widget-Id": "mealie-mealplan"}
    ).status_code == 200
    denied = mealie_client.get(
        "/api/mealie/mealplan", headers={"X-Widget-Id": "fremd-widget"}
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN"
    unknown = mealie_client.get(
        "/api/mealie/mealplan", headers={"X-Widget-Id": "gibt-es-nicht"}
    )
    assert unknown.status_code == 404


# --- Kriterium 2: Widget `mealie-mealplan` ist vollstaendig ------------------

def test_widget_mealie_mealplan_is_complete():
    manifest = json.loads((WIDGET_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "mealie-mealplan"
    assert manifest["permissions"] == ["mealie"]
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
