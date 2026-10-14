"""Integrationsschritt WP-P2-INT (Plan 10.1/10.2, spec-phase2 E.3).

Belegt die Zusammenfuehrung der gemeinsamen Datei `backend/app/main.py`:
alle neuen Router der WP-P2-1..P2-4 sind registriert (und `install` **vor**
`widgets`, sonst faengt `GET/PATCH /widgets/{widget_id}` den Install-Pfad ab),
und `/widgets` liefert installierte **und** eingebaute Widgets aus.
"""

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import get_settings
from app.main import create_app


def _manifest(widget_id: str) -> dict:
    return {
        "id": widget_id,
        "name": "Demo",
        "version": "1.0.0",
        "author": "Test",
        "category": "general",
        "permissions": [],
        "default_size": {"w": 2, "h": 2},
        "max_instances": 1,
    }


def _widget_zip(widget_id: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{widget_id}/manifest.json", json.dumps(_manifest(widget_id)))
        archive.writestr(
            f"{widget_id}/frontend/widget.js", "export default class Widget {}\n"
        )
    return buffer.getvalue()


@pytest.fixture
def integrated_client(tmp_path, monkeypatch):
    """App aus `create_app()` mit gesetztem Install-Ordner (`WIDGETS_INSTALL_DIR`)."""
    install = tmp_path / "installed"
    install.mkdir()
    monkeypatch.setenv("WIDGETS_INSTALL_DIR", str(install))
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "dashboard.db"))
    get_settings.cache_clear()
    db_module.init_db()
    app = create_app()
    with TestClient(app) as client:
        yield client, install
    get_settings.cache_clear()


def test_new_routers_registered(integrated_client):
    """Baikal/Grocy/Mealie liefern ohne Zugangsdaten 200 mit `error_code`."""
    client, _ = integrated_client

    baikal = client.get("/api/baikal/events")
    assert baikal.status_code == 200, baikal.text
    assert baikal.json()["error_code"] == "BAIKAL_UNAVAILABLE"

    grocy = client.get("/api/grocy/stock")
    assert grocy.status_code == 200, grocy.text
    assert grocy.json()["error_code"] == "GROCY_UNAVAILABLE"

    mealie = client.get("/api/mealie/mealplan")
    assert mealie.status_code == 200, mealie.text
    assert mealie.json()["error_code"] == "MEALIE_UNAVAILABLE"


def test_install_route_registered_before_widgets(integrated_client):
    """`POST /api/widgets/install` gewinnt gegen `/widgets/{widget_id}` (sonst 405)."""
    client, install_dir = integrated_client

    response = client.post(
        "/api/widgets/install",
        files={"file": ("widget.zip", _widget_zip("demo-widget"), "application/zip")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["widget"]["id"] == "demo-widget"
    assert (install_dir / "demo-widget" / "frontend" / "widget.js").is_file()


def test_static_mount_serves_installed_and_builtin(integrated_client):
    """`/widgets/<id>/…` liefert installierte und eingebaute Widgets."""
    client, install_dir = integrated_client

    (install_dir / "ext-w" / "frontend").mkdir(parents=True)
    (install_dir / "ext-w" / "frontend" / "widget.js").write_text(
        "export default class Widget {}\n", encoding="utf-8"
    )

    installed = client.get("/widgets/ext-w/frontend/widget.js")
    assert installed.status_code == 200, installed.text
    assert "class Widget" in installed.text

    # Eingebaute Widgets kommen weiterhin aus dem Repo-`widgets/`-Ordner.
    builtin = client.get("/widgets/hermes-todos/manifest.json")
    assert builtin.status_code == 200, builtin.text
    assert builtin.json()["id"] == "hermes-todos"
