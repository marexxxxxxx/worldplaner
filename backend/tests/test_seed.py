"""Seeding der eingebauten Widgets und der Standardplatzierung (Spezifikation C).

Jeder Test schreibt seine Manifeste in einen isolierten `WIDGETS_DIR`, bevor die
App (und damit der Lifespan -> `init_db`) gestartet wird.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


@pytest.fixture
def widgets(tmp_path, monkeypatch):
    """Isolierte Umgebung: eigene DB plus eigener, zunaechst leerer Widget-Ordner."""
    root = tmp_path / "widgets"
    root.mkdir()
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "dashboard.db"))
    monkeypatch.setenv("WIDGETS_DIR", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


def write_manifest(root, widget_id, **overrides):
    manifest = {
        "id": widget_id,
        "name": widget_id,
        "version": "1.0.0",
        "category": "general",
        "default_size": {"w": 4, "h": 2},
    }
    manifest.update(overrides)
    directory = root / widget_id
    directory.mkdir(exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def widget_by_id(client, widget_id):
    return next(w for w in client.get("/api/widgets").json() if w["id"] == widget_id)


def test_registers_builtins_with_is_builtin(widgets):
    write_manifest(widgets, "hermes-todos", name="Hermes-To-Dos", category="productivity")

    with TestClient(create_app()) as client:
        entry = widget_by_id(client, "hermes-todos")
        assert entry["is_builtin"] is True
        assert entry["is_enabled"] is True
        assert entry["name"] == "Hermes-To-Dos"
        assert entry["max_instances"] == 1

        detail = client.get("/api/widgets/hermes-todos").json()
        assert detail["manifest"]["id"] == "hermes-todos"
        assert detail["manifest"]["default_size"] == {"w": 4, "h": 2}


def test_registers_widgets_from_manifest_not_only_frontend(widgets):
    """Der BEFUND war `GET /api/widgets == []` — jetzt liefert er die Manifeste."""
    write_manifest(widgets, "hermes-todos", name="Hermes-To-Dos", default_size={"w": 12, "h": 6})

    with TestClient(create_app()) as client:
        assert [w["id"] for w in client.get("/api/widgets").json()] == ["hermes-todos"]
        assert client.get("/api/widgets").json()[0]["is_builtin"] is True


def test_default_layout_uses_tab_mapping_and_default_size(widgets):
    write_manifest(widgets, "alpha", default_size={"w": 6, "h": 2})
    write_manifest(widgets, "beta", default_size={"w": 6, "h": 3})
    write_manifest(widgets, "hermes-todos", default_size={"w": 12, "h": 6})

    with TestClient(create_app()) as client:
        todos = client.get("/api/layout", params={"tab": "todos"}).json()
        assert len(todos) == 1
        assert todos[0]["widget_id"] == "hermes-todos"
        assert (todos[0]["x"], todos[0]["y"], todos[0]["w"], todos[0]["h"]) == (0, 0, 12, 6)

        dashboard = client.get("/api/layout", params={"tab": "dashboard"}).json()
        assert [(row["widget_id"], row["y"]) for row in dashboard] == [("alpha", 0), ("beta", 2)]
        assert [row["h"] for row in dashboard] == [2, 3]


def test_seeded_instance_is_updatable_via_put(widgets):
    """Damit persistiert Verschieben/Vergroessern im Bento-Grid (D.3/E.6)."""
    write_manifest(widgets, "hermes-todos", default_size={"w": 12, "h": 6})

    with TestClient(create_app()) as client:
        instance = client.get("/api/layout", params={"tab": "todos"}).json()[0]
        response = client.put(f"/api/layout/{instance['id']}", json={"h": 5})
        assert response.status_code == 200
        assert response.json()["h"] == 5
        persisted = client.get("/api/layout", params={"tab": "todos"}).json()[0]
        assert (persisted["id"], persisted["h"]) == (instance["id"], 5)


def test_reseed_preserves_state_and_does_not_duplicate_layout(widgets):
    write_manifest(widgets, "hermes-todos", default_size={"w": 12, "h": 6})

    with TestClient(create_app()) as client:
        client.patch("/api/widgets/hermes-todos", json={"is_enabled": False, "config": {"filter": "done"}})
        first = client.get("/api/layout", params={"tab": "todos"}).json()

    with TestClient(create_app()) as client:
        entry = widget_by_id(client, "hermes-todos")
        assert entry["is_enabled"] is False
        assert client.get("/api/widgets/hermes-todos").json()["config"] == {"filter": "done"}
        assert client.get("/api/layout", params={"tab": "todos"}).json() == first


def test_reseed_refreshes_manifest_metadata(widgets):
    write_manifest(widgets, "demo", version="1.0.0", name="Alt")

    with TestClient(create_app()) as client:
        assert widget_by_id(client, "demo")["version"] == "1.0.0"

    write_manifest(widgets, "demo", version="2.0.0", name="Neu")

    with TestClient(create_app()) as client:
        entry = widget_by_id(client, "demo")
        assert (entry["version"], entry["name"]) == ("2.0.0", "Neu")
        assert client.get("/api/widgets/demo").json()["manifest"]["version"] == "2.0.0"


def test_invalid_manifest_is_skipped(widgets):
    (widgets / "broken").mkdir()
    (widgets / "broken" / "manifest.json").write_text("{kein json", encoding="utf-8")
    write_manifest(widgets, "ok")

    with TestClient(create_app()) as client:
        assert [w["id"] for w in client.get("/api/widgets").json()] == ["ok"]
        assert len(client.get("/api/layout", params={"tab": "dashboard"}).json()) == 1


def test_without_builtins_registry_stays_empty(widgets):
    """Leerer Widget-Ordner: keine erfundenen Eintraege, keine Layout-Zeilen."""
    with TestClient(create_app()) as client:
        assert client.get("/api/widgets").json() == []
        assert client.get("/api/layout", params={"tab": "todos"}).json() == []
