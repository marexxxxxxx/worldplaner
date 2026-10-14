"""Dashboard-Zustand (Spezifikation D.7).

Zusaetzlich der Fokus-Zustand (spec-focus C/D.1, WP-F1-1): Key `focus` im
bestehenden Lager `dashboard_state` — keine neue Tabelle, kein Schreibzugriff
auf `dashboard_layout`. Die Literal-Routen `/state/focus` sind VOR der
generischen `/{key}`-Route registriert, sonst faengt `/{key}` den Literal ab.
"""

import json
import sqlite3

from fastapi import APIRouter, Depends

from .. import db
from ..models import FocusUpdate, StateUpdate, not_found, validation_error
from ..permissions import require_permission
from .events import bus

ALLOWED_KEYS = ("active_tab", "brightness", "screen")

# Fokus-Schluessel im generischen State-Lager (spec-focus C).
FOCUS_KEY = "focus"

router = APIRouter(prefix="/state", tags=["state"])


def _upsert_state(conn: sqlite3.Connection, key: str, value: object, now: str) -> None:
    """Schreibt `value` (JSON) unter `key` — INSERT oder UPDATE (C)."""
    conn.execute(
        "INSERT INTO dashboard_state (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, json.dumps(value), now),
    )


def _resolve_focus(
    conn: sqlite3.Connection, widget_id: str, instance_id: str | None
) -> tuple[str, str]:
    """Loest die Ziel-Kachel zu `(instance_id, tab_id)` auf (spec-focus C/D.1).

    `instance_id` ist die `dashboard_layout.id` — im Fokus-Objekt `instance_id`
    genannt, von `GET /api/layout` als `id` geliefert.

    Ohne `instance_id` gewinnt die erste Layout-Instanz des Widgets ueber ALLE
    Tabs (`ORDER BY tab.position, y, x`). Keine Instanz -> 404 NOT_FOUND; ein
    gesetztes `instance_id`, das nicht existiert oder zu einem anderen Widget
    gehoert -> 400 VALIDATION_ERROR.
    """
    if instance_id is not None:
        row = conn.execute(
            "SELECT id, tab_id FROM dashboard_layout WHERE id = ? AND widget_id = ?",
            (instance_id, widget_id),
        ).fetchone()
        if row is None:
            raise validation_error(
                f"Layout-Instanz {instance_id} gehoert nicht zu Widget {widget_id}"
            )
        return row["id"], row["tab_id"]
    row = conn.execute(
        "SELECT l.id AS id, l.tab_id AS tab_id FROM dashboard_layout AS l "
        "JOIN tabs AS t ON t.id = l.tab_id "
        "WHERE l.widget_id = ? ORDER BY t.position, l.y, l.x LIMIT 1",
        (widget_id,),
    ).fetchone()
    if row is None:
        raise not_found(f"Widget {widget_id} ist auf keinem Tab platziert")
    return row["id"], row["tab_id"]


@router.get("/focus")
async def get_focus() -> dict:
    """Aktueller Fokus oder `null` (spec-focus D.1).

    Kein `state`-Permission-Gate (wie die bestehenden PUT-Routen): Zugriff
    erfolgt same-origin lokal bzw. ueber den MCP-Server. Fehlt die Zeile auf
    einer frischen DB, ist das Ergebnis `value: null` (kein 404).
    """
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM dashboard_state WHERE key = ?", (FOCUS_KEY,)
        ).fetchone()
    return {"key": FOCUS_KEY, "value": json.loads(row["value"]) if row is not None else None}


@router.put("/focus")
async def put_focus(payload: FocusUpdate) -> dict:
    """Setzt den Fokus und waehlt den Tab der Ziel-Kachel (spec-focus D.1)."""
    now = db.utc_now()
    with db.get_conn() as conn:
        instance_id, tab_id = _resolve_focus(conn, payload.widget_id, payload.instance_id)
        focus = {
            "widget_id": payload.widget_id,
            "instance_id": instance_id,
            "tab_id": tab_id,
            "mode": payload.mode,
            "ts": now,
        }
        _upsert_state(conn, FOCUS_KEY, focus, now)
        _upsert_state(conn, "active_tab", tab_id, now)
    bus.publish("focus.changed", {"focus": focus})
    return {"focus": focus}


@router.delete("/focus")
async def delete_focus() -> dict:
    """Setzt den Fokus zurueck (`value: null`) und publiziert `focus.changed`."""
    with db.get_conn() as conn:
        _upsert_state(conn, FOCUS_KEY, None, db.utc_now())
    bus.publish("focus.changed", {"focus": None})
    return {"focus": None}


@router.get("/{key}")
async def get_state(
    key: str,
    _perm: None = Depends(require_permission("state")),
) -> dict:
    if key not in ALLOWED_KEYS:
        raise not_found(f"Unbekannter State-Key {key}")
    with db.get_conn() as conn:
        row = conn.execute("SELECT value FROM dashboard_state WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise not_found(f"State {key} nicht gesetzt")
    return {"key": key, "value": json.loads(row["value"])}


@router.put("/{key}")
async def put_state(key: str, payload: StateUpdate) -> dict:
    if key not in ALLOWED_KEYS:
        raise validation_error(f"Unbekannter State-Key {key}")
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO dashboard_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(payload.value), db.utc_now()),
        )
    if key == "screen":
        bus.publish("screen.changed", {"screen": payload.value})
    return {"key": key, "value": payload.value}
