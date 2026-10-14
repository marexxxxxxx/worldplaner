"""Zip-/Git-Widget-Installation (spec-phase2 D/WP-P2-4).

Belegt die binaeren Faelle 1-6: Registrierung + statische Auslieferung von
`frontend/widget.js`, Deinstallation (Zeile + Ordner), Zip-Haertung
(`../`/absoluter Pfad/ungueltiges Manifest -> 400 ohne Schreiben),
`id`-Kollision (409), Git-Installation (shallow clone) und Clone-Fehler ohne
Teilreste. Der `install`-Router ist in `main.py` noch nicht registriert (das
macht der Integrationsschritt, Plan 10.2); darum wird hier eine lokale Test-App
mit denselben Fehler-Handlern gebaut.
"""

import io
import json
import subprocess
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import get_settings
from app.main import _register_error_handlers
from app.routers import install, layout, widgets

WIDGET_ID = "demo-widget"
GIT_WIDGET_ID = "git-widget"


def _manifest(widget_id=WIDGET_ID, **overrides):
    data = {
        "id": widget_id,
        "name": "Demo",
        "version": "1.0.0",
        "author": "Test",
        "category": "general",
        "permissions": [],
        "default_size": {"w": 2, "h": 2},
        "max_instances": 1,
    }
    data.update(overrides)
    return data


def zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def widget_zip(widget_id=WIDGET_ID, manifest=None) -> bytes:
    manifest = manifest if manifest is not None else _manifest(widget_id)
    return zip_bytes(
        {
            f"{widget_id}/manifest.json": json.dumps(manifest),
            f"{widget_id}/frontend/widget.js": "export default class Widget {}\n",
            f"{widget_id}/frontend/style.css": ".demo-widget {}\n",
        }
    )


def seed_widget(conn, widget_id=WIDGET_ID, is_builtin=0):
    conn.execute(
        "INSERT INTO widgets "
        "(id, name, version, category, icon, is_builtin, is_enabled, max_instances, "
        " manifest_json, config_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (widget_id, "Seed", "1.0.0", "general", "", is_builtin, 1, 1,
         json.dumps(_manifest(widget_id)), "{}"),
    )


def git_repo(tmp_path, widget_id=GIT_WIDGET_ID):
    """Legt ein minimales lokales Git-Repo (Branch `main`) mit Widget-Dateien an."""
    repo = tmp_path / "repo"
    (repo / "frontend").mkdir(parents=True)
    (repo / "manifest.json").write_text(json.dumps(_manifest(widget_id)), encoding="utf-8")
    (repo / "frontend" / "widget.js").write_text(
        "export default class Widget {}\n", encoding="utf-8"
    )
    run = ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test"]
    subprocess.run([*run, "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run([*run, "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run([*run, "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    return repo


@pytest.fixture
def install_env(tmp_path, monkeypatch):
    path = tmp_path / "installed"
    path.mkdir()
    monkeypatch.setenv("WIDGETS_INSTALL_DIR", str(path))
    monkeypatch.setenv("WIDGETS_MAX_ZIP_BYTES", "10485760")
    monkeypatch.setenv("WIDGETS_MAX_UNCOMPRESSED_BYTES", "52428800")
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


@pytest.fixture
def install_client(db_path, install_env):
    db_module.init_db()
    app = FastAPI()
    _register_error_handlers(app)
    app.include_router(install.router, prefix="/api")
    app.include_router(widgets.router, prefix="/api")
    app.include_router(layout.router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


def _static_client(directory):
    app = FastAPI()
    app.mount("/widgets", StaticFiles(directory=str(directory)))
    return TestClient(app)


def _post_zip(client, data):
    return client.post(
        "/api/widgets/install", files={"file": ("widget.zip", data, "application/zip")}
    )


# --- Kriterium 1: Registrierung + Auslieferung ------------------------------


def test_install_zip_registers_and_serves_widget_js(install_client, install_env):
    response = _post_zip(install_client, widget_zip())
    assert response.status_code == 201, response.text
    summary = response.json()["widget"]
    assert summary["id"] == WIDGET_ID
    assert summary["is_builtin"] is False
    assert summary["is_enabled"] is True

    listing = install_client.get("/api/widgets").json()
    assert [row["id"] for row in listing] == [WIDGET_ID]

    # Ordner WIDGETS_INSTALL_DIR/<id>/ inkl. frontend/widget.js
    widget_js = install_env / WIDGET_ID / "frontend" / "widget.js"
    assert widget_js.is_file()
    assert "class Widget" in widget_js.read_text(encoding="utf-8")

    # Das Dashboard laedt `/widgets/<id>/frontend/widget.js` (D10 Static-Mount).
    with _static_client(install_env) as static_client:
        served = static_client.get(f"/widgets/{WIDGET_ID}/frontend/widget.js")
    assert served.status_code == 200
    assert "class Widget" in served.text

    # Nach Platzierung erscheint das Widget auch im dashboard-Tab.
    created = install_client.post(
        "/api/layout",
        json={"widget_id": WIDGET_ID, "tab_id": "dashboard", "x": 0, "y": 0, "w": 2, "h": 2},
    )
    assert created.status_code == 201, created.text
    instances = install_client.get("/api/layout", params={"tab": "dashboard"}).json()
    assert any(row["widget_id"] == WIDGET_ID for row in instances)


# --- Kriterium 2: Deinstallation --------------------------------------------


def test_delete_removes_row_and_folder(install_client, install_env):
    _post_zip(install_client, widget_zip())
    assert (install_env / WIDGET_ID).is_dir()

    response = install_client.delete(f"/api/widgets/{WIDGET_ID}")
    assert response.status_code == 204
    assert install_client.get("/api/widgets").json() == []
    assert not (install_env / WIDGET_ID).exists()


def test_delete_builtin_forbidden(install_client, conn):
    seed_widget(conn, widget_id="builtin-w", is_builtin=1)
    response = install_client.delete("/api/widgets/builtin-w")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    # Zeile bleibt bestehen.
    assert any(row["id"] == "builtin-w" for row in install_client.get("/api/widgets").json())


def test_delete_unknown_not_found(install_client):
    response = install_client.delete("/api/widgets/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# --- Kriterium 3: Zip-Haertung ----------------------------------------------


def test_zip_path_traversal_rejected_without_writing(install_client, install_env):
    data = zip_bytes(
        {
            "../evil.txt": "pwned",
            f"{WIDGET_ID}/manifest.json": json.dumps(_manifest()),
        }
    )
    response = _post_zip(install_client, data)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert list(install_env.iterdir()) == []


def test_zip_absolute_path_rejected(install_client, install_env):
    data = zip_bytes(
        {
            "/etc/passwd": "x",
            f"{WIDGET_ID}/manifest.json": json.dumps(_manifest()),
        }
    )
    response = _post_zip(install_client, data)
    assert response.status_code == 400
    assert list(install_env.iterdir()) == []


def test_zip_invalid_manifest_rejected(install_client, install_env):
    # Pflichtfeld `default_size` fehlt.
    broken = {"id": WIDGET_ID, "name": "Demo", "version": "1.0.0", "category": "general"}
    data = zip_bytes({f"{WIDGET_ID}/manifest.json": json.dumps(broken)})
    response = _post_zip(install_client, data)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert list(install_env.iterdir()) == []


def test_zip_manifest_not_json_rejected(install_client, install_env):
    data = zip_bytes({f"{WIDGET_ID}/manifest.json": "not json"})
    response = _post_zip(install_client, data)
    assert response.status_code == 400
    assert list(install_env.iterdir()) == []


def test_zip_not_a_zip_rejected(install_client, install_env):
    response = _post_zip(install_client, b"definitely not a zip")
    assert response.status_code == 400
    assert list(install_env.iterdir()) == []


def test_zip_over_max_bytes_rejected(install_client, install_env, monkeypatch):
    monkeypatch.setenv("WIDGETS_MAX_ZIP_BYTES", "10")
    get_settings.cache_clear()
    response = _post_zip(install_client, widget_zip())
    assert response.status_code == 400
    assert list(install_env.iterdir()) == []


def test_zip_folder_id_mismatch_rejected(install_client, install_env):
    # Ordner heisst anders als die Manifest-id.
    data = zip_bytes({"other-name/manifest.json": json.dumps(_manifest(WIDGET_ID))})
    response = _post_zip(install_client, data)
    assert response.status_code == 400
    assert list(install_env.iterdir()) == []


# --- Kriterium 4: Kollision + id-Muster -------------------------------------


def test_install_conflict_existing_row(install_client, install_env, conn):
    seed_widget(conn, widget_id=WIDGET_ID, is_builtin=1)
    response = _post_zip(install_client, widget_zip())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    assert not (install_env / WIDGET_ID).exists()


def test_install_invalid_id_rejected(install_client, install_env):
    data = zip_bytes({"Bad_ID/manifest.json": json.dumps(_manifest("Bad_ID"))})
    response = _post_zip(install_client, data)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert list(install_env.iterdir()) == []


# --- Kriterium 5: Git-Installation ------------------------------------------


def test_install_git_clones_and_registers(install_client, install_env, tmp_path):
    repo = git_repo(tmp_path)
    response = install_client.post(
        "/api/widgets/install/git", json={"url": str(repo), "ref": "main"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["widget"]["id"] == GIT_WIDGET_ID
    assert response.json()["widget"]["is_builtin"] is False
    installed = install_env / GIT_WIDGET_ID
    assert (installed / "frontend" / "widget.js").is_file()
    assert (installed / "manifest.json").is_file()
    # `.git` bleibt nicht im Install-Ordner zurueck.
    assert not (installed / ".git").exists()
    assert any(row["id"] == GIT_WIDGET_ID for row in install_client.get("/api/widgets").json())


def test_install_git_clone_failure_writes_nothing(install_client, install_env, tmp_path):
    response = install_client.post(
        "/api/widgets/install/git",
        json={"url": str(tmp_path / "missing-repo"), "ref": "main"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert list(install_env.iterdir()) == []


def test_install_git_rejects_unsupported_scheme(install_client):
    response = install_client.post(
        "/api/widgets/install/git", json={"url": "ftp://example/repo.git", "ref": "main"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_install_git_rejects_unknown_field(install_client):
    response = install_client.post(
        "/api/widgets/install/git", json={"url": "https://example/repo.git", "ref": "main", "x": 1}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
