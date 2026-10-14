"""SQLite-Anbindung: setzt pro Verbindung WAL, busy_timeout und foreign_keys (Spezifikation A.4/C).

Einzige DB-Datei: dashboard.db (Volume sqlite-data, /data).
"""

import json
import logging
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings

log = logging.getLogger(__name__)

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

# Ziel-Tab der eingebauten Widgets fuer die Standardplatzierung (Spezifikation C).
# Das Manifest (E.2) traegt kein Tab-Feld; das Frontend spiegelt die Zuordnung in
# `BUILTIN_WIDGETS` (frontend/js/app.js). Nicht gelistete Widgets landen auf dem
# Dashboard. Die konkrete Standardanordnung ist eine Setup-Frage (L.3 Punkt 5).
BUILTIN_TAB = {"hermes-todos": "todos"}
DEFAULT_TAB = "dashboard"

_REQUIRED_MANIFEST_FIELDS = ("id", "name", "version", "category", "default_size")


def _widgets_root() -> Path:
    """Ordner mit `widgets/*/manifest.json` (Abschnitt C)."""
    configured = get_settings().widgets_dir
    if configured:
        return Path(configured)
    # backend/app/db.py -> Repo-Wurzel; im Container /app/app/db.py -> /.
    return Path(__file__).resolve().parent.parent.parent / "widgets"


def _valid_manifest(data: object, path: Path) -> bool:
    """Prueft die Pflichtfelder, die fuer Registry und Standardplatzierung noetig sind."""
    if not isinstance(data, dict):
        log.warning("Widget-Manifest %s ist kein JSON-Objekt", path)
        return False
    missing = [field for field in _REQUIRED_MANIFEST_FIELDS if field not in data]
    if missing:
        log.warning("Widget-Manifest %s fehlen Pflichtfelder: %s", path, missing)
        return False
    if not all(isinstance(data[field], str) for field in ("id", "name", "version", "category")):
        log.warning("Widget-Manifest %s hat nicht-textuelle Pflichtfelder", path)
        return False
    size = data["default_size"]
    if not isinstance(size, dict) or not all(
        isinstance(size.get(axis), int) and size[axis] >= 1 for axis in ("w", "h")
    ):
        log.warning("Widget-Manifest %s hat ungueltige default_size", path)
        return False
    max_instances = data.get("max_instances", 1)
    if not isinstance(max_instances, int) or max_instances < 1:
        log.warning("Widget-Manifest %s hat ungueltige max_instances", path)
        return False
    return True


def _load_manifests(root: Path) -> list[dict]:
    """Liest alle `widgets/*/manifest.json` in Standardreihenfolge (sortiert nach Ordner)."""
    if not root.is_dir():
        return []
    manifests: list[dict] = []
    for path in sorted(root.glob("*/manifest.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Widget-Manifest %s nicht lesbar: %s", path, exc)
            continue
        if _valid_manifest(data, path):
            manifests.append(data)
    return manifests


def _seed_default_layout(conn: sqlite3.Connection, manifests: list[dict], now: str) -> None:
    """Legt je eingebautem Widget eine Standardinstanz aus `default_size` an (Abschnitt C)."""
    y_by_tab: dict[str, int] = {}
    for manifest in manifests:
        tab_id = BUILTIN_TAB.get(manifest["id"], DEFAULT_TAB)
        if conn.execute("SELECT 1 FROM tabs WHERE id = ?", (tab_id,)).fetchone() is None:
            log.warning("Ziel-Tab %s fuer Widget %s fehlt - Standardinstanz uebersprungen",
                        tab_id, manifest["id"])
            continue
        size = manifest["default_size"]
        y = y_by_tab.get(tab_id, 0)
        conn.execute(
            "INSERT INTO dashboard_layout "
            "(id, widget_id, tab_id, x, y, w, h, config_override, created_at, updated_at) "
            "VALUES (?, ?, ?, 0, ?, ?, ?, '{}', ?, ?)",
            (str(uuid.uuid4()), manifest["id"], tab_id, y, size["w"], size["h"], now, now),
        )
        y_by_tab[tab_id] = y + size["h"]


def seed_builtin_widgets(conn: sqlite3.Connection) -> None:
    """Registriert eingebaute Widgets aus `widgets/*/manifest.json` (Abschnitt C).

    Upsert per `id` mit `is_builtin=1`; bestehende `is_enabled`/`config_json`-
    Werte bleiben erhalten. Beim Erststart (leeres `dashboard_layout`) wird je
    eingebautem Widget eine Standardinstanz aus `default_size` erzeugt.
    """
    manifests = _load_manifests(_widgets_root())
    if not manifests:
        return
    now = utc_now()
    for manifest in manifests:
        conn.execute(
            "INSERT INTO widgets "
            "(id, name, version, author, description, category, icon, is_builtin, "
            " is_enabled, max_instances, manifest_json, config_json, installed_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, '{}', ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "name = excluded.name, version = excluded.version, author = excluded.author, "
            "description = excluded.description, category = excluded.category, "
            "icon = excluded.icon, is_builtin = 1, "
            "max_instances = excluded.max_instances, manifest_json = excluded.manifest_json, "
            "updated_at = excluded.updated_at",
            (
                manifest["id"],
                manifest["name"],
                manifest["version"],
                manifest.get("author", ""),
                manifest.get("description", ""),
                manifest["category"],
                manifest.get("icon", ""),
                manifest.get("max_instances", 1),
                json.dumps(manifest),
                now,
                now,
            ),
        )
    if conn.execute("SELECT COUNT(*) AS c FROM dashboard_layout").fetchone()["c"] == 0:
        _seed_default_layout(conn, manifests, now)


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
    """Legt das Schema idempotent an, seedet Tabs und eingebaute Widgets (Abschnitt C)."""
    with get_conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        if conn.execute("SELECT COUNT(*) AS c FROM tabs").fetchone()["c"] == 0:
            conn.executemany(
                "INSERT INTO tabs (id, slug, title, position) VALUES (?, ?, ?, ?)",
                TAB_SEED,
            )
        seed_builtin_widgets(conn)


def ping() -> bool:
    """Health-Pruefung: wirft bei nicht erreichbarer DB."""
    with get_conn() as conn:
        conn.execute("SELECT 1")
    return True
