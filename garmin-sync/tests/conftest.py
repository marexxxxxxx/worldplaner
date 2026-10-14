"""Fixtures fuer die garmin-sync-Tests.

Die Test-DB nutzt das **echte** Backend-Schema (`backend/app/schema.sql`), damit
der einzige Schreiber von `garmin_cache` gegen genau die Tabelle schreibt, die
das Backend liest (A.2/D4).
"""

import sys
from pathlib import Path

import pytest

GARMIN_SYNC_DIR = Path(__file__).resolve().parent.parent
if str(GARMIN_SYNC_DIR) not in sys.path:
    sys.path.insert(0, str(GARMIN_SYNC_DIR))

SCHEMA_PATH = GARMIN_SYNC_DIR.parent / "backend" / "app" / "schema.sql"

import sync as gs  # noqa: E402


@pytest.fixture
def schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


@pytest.fixture
def conn(tmp_path):
    """Leere Test-DB mit dem Produktionsschema."""
    connection = gs.connect_db(str(tmp_path / "dashboard.db"))
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    yield connection
    connection.close()


@pytest.fixture
def db_path(tmp_path) -> str:
    """Pfad einer Test-DB mit Produktionsschema (fuer den Write-Server).

    Der Write-Server oeffnet pro Vorgang eine eigene Verbindung; deshalb wird
    hier nur der Dateipfad weitergereicht, nicht eine offene Verbindung.
    """
    path = tmp_path / "dashboard.db"
    connection = gs.connect_db(str(path))
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    connection.close()
    return str(path)
