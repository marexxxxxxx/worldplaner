"""SQLite-Anbindung: setzt pro Verbindung WAL, busy_timeout und foreign_keys (Spezifikation A.4/C).

Einzige DB-Datei: dashboard.db (Volume sqlite-data, /data).
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def utc_now() -> str:
    """Zeitstempel in der verbindlichen Konvention '%Y-%m-%dT%H:%M:%SZ' (UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Seed-Daten fuer die Navigation (Spezifikation C, beim Erststart wenn 'tabs' leer).
TAB_SEED = [
    ("dashboard", "dashboard", "Dashboard", 0),
    ("todos", "todos", "To-Dos", 1),
    ("widgets", "widgets", "Widgets verwalten", 2),
]


def connect() -> sqlite3.Connection:
    """Oeffnet eine Verbindung und setzt die verbindlichen PRAGMAs."""
    path = Path(get_settings().sqlite_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Verbindung pro Zugriff; committet bei Erfolg, schliesst immer."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Legt das Schema idempotent an und seedet die Tabs beim Erststart."""
    with get_conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        if conn.execute("SELECT COUNT(*) AS c FROM tabs").fetchone()["c"] == 0:
            conn.executemany(
                "INSERT INTO tabs (id, slug, title, position) VALUES (?, ?, ?, ?)",
                TAB_SEED,
            )


def ping() -> bool:
    """Health-Pruefung: wirft bei nicht erreichbarer DB."""
    with get_conn() as conn:
        conn.execute("SELECT 1")
    return True
