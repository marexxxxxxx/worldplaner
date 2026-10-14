"""Gemeinsame Fixtures: jede Testdatei arbeitet gegen eine frische, temporaere DB."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import db as db_module  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
def widgets_dir(tmp_path, monkeypatch):
    """Isolierter, standardmaessig leerer Widget-Ordner (Abschnitt C)."""
    path = tmp_path / "widgets"
    path.mkdir()
    monkeypatch.setenv("WIDGETS_DIR", str(path))
    get_settings.cache_clear()
    return path


@pytest.fixture
def db_path(tmp_path, widgets_dir, monkeypatch):
    path = tmp_path / "dashboard.db"
    monkeypatch.setenv("SQLITE_PATH", str(path))
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


@pytest.fixture
def client(db_path):
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def conn(db_path):
    """Direkte Verbindung (Autocommit) auf dieselbe Test-DB, zum Seeden/Pruefen."""
    db_module.init_db()
    connection = db_module.connect()
    connection.isolation_level = None
    yield connection
    connection.close()
