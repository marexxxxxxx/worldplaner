"""Widgets (Spezifikation D.2)."""

import json

from fastapi import APIRouter

from .. import db
from ..models import WidgetPatch, forbidden, not_found
from ..services import install as install_service

router = APIRouter(prefix="/widgets", tags=["widgets"])


def _summary(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "version": row["version"],
        "category": row["category"],
        "icon": row["icon"],
        "is_enabled": bool(row["is_enabled"]),
        "is_builtin": bool(row["is_builtin"]),
        "max_instances": row["max_instances"],
    }


@router.get("")
async def list_widgets() -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM widgets ORDER BY id").fetchall()
    return [_summary(row) for row in rows]


@router.get("/{widget_id}")
async def get_widget(widget_id: str) -> dict:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM widgets WHERE id = ?", (widget_id,)).fetchone()
    if row is None:
        raise not_found(f"Widget {widget_id} nicht gefunden")
    return {
        "manifest": json.loads(row["manifest_json"]),
        "config": json.loads(row["config_json"]),
        "is_enabled": bool(row["is_enabled"]),
    }


@router.patch("/{widget_id}")
async def patch_widget(widget_id: str, payload: WidgetPatch) -> dict:
    with db.get_conn() as conn:
        if conn.execute("SELECT 1 FROM widgets WHERE id = ?", (widget_id,)).fetchone() is None:
            raise not_found(f"Widget {widget_id} nicht gefunden")
        sets: list[str] = []
        values: list[object] = []
        if payload.is_enabled is not None:
            sets.append("is_enabled = ?")
            values.append(1 if payload.is_enabled else 0)
        if payload.config is not None:
            sets.append("config_json = ?")
            values.append(json.dumps(payload.config))
        sets.append("updated_at = ?")
        values.append(db.utc_now())
        values.append(widget_id)
        conn.execute(f"UPDATE widgets SET {', '.join(sets)} WHERE id = ?", values)
        row = conn.execute("SELECT * FROM widgets WHERE id = ?", (widget_id,)).fetchone()
    return _summary(row)


@router.delete("/{widget_id}", status_code=204)
async def delete_widget(widget_id: str) -> None:
    """Deinstalliert ein nicht eingebautes Widget (spec-phase2 D/WP-P2-4).

    Eingebaute Widgets (`is_builtin=1`) sind nie deinstallierbar (`403`),
    unbekannte `id` -> `404`; sonst Zeile + Ordner entfernen (`204`).
    """
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT is_builtin FROM widgets WHERE id = ?", (widget_id,)
        ).fetchone()
        if row is None:
            raise not_found(f"Widget {widget_id} nicht gefunden")
        if row["is_builtin"]:
            raise forbidden(f"Widget {widget_id} ist eingebaut und kann nicht deinstalliert werden")
        conn.execute("DELETE FROM widgets WHERE id = ?", (widget_id,))
    install_service.remove(widget_id)
