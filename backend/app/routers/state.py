"""Dashboard-Zustand (Spezifikation D.7)."""

import json

from fastapi import APIRouter

from .. import db
from ..models import StateUpdate, not_found, validation_error
from .events import bus

ALLOWED_KEYS = ("active_tab", "brightness", "screen")

router = APIRouter(prefix="/state", tags=["state"])


@router.get("/{key}")
async def get_state(key: str) -> dict:
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
