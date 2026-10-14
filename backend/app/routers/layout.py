"""Bento-Layout (Spezifikation D.3).

Bounds: 0 <= x, x + w <= grid_columns, y >= 0, w,h >= 1.
Overlap auf demselben tab_id -> 409 CONFLICT (D.0 + UNIQUE(tab_id,x,y)).
"""

import json
import sqlite3
import uuid

from fastapi import APIRouter, Query

from .. import db
from ..config import get_settings
from ..models import LayoutCreate, LayoutUpdate, conflict, not_found, validation_error

router = APIRouter(prefix="/layout", tags=["layout"])


def _instance(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "widget_id": row["widget_id"],
        "tab_id": row["tab_id"],
        "x": row["x"],
        "y": row["y"],
        "w": row["w"],
        "h": row["h"],
        "config_override": json.loads(row["config_override"]),
    }


def _check_bounds(x: int, y: int, w: int, h: int) -> None:
    columns = get_settings().grid_columns
    if x < 0 or y < 0 or w < 1 or h < 1 or x + w > columns:
        raise validation_error(f"Layout ausserhalb des Rasters (0 <= x, x + w <= {columns})")


def _overlaps(conn: sqlite3.Connection, tab_id: str, x: int, y: int, w: int, h: int,
              exclude_id: str | None = None) -> bool:
    rows = conn.execute(
        "SELECT id, x, y, w, h FROM dashboard_layout WHERE tab_id = ?", (tab_id,)
    ).fetchall()
    for row in rows:
        if row["id"] == exclude_id:
            continue
        if not (x + w <= row["x"] or row["x"] + row["w"] <= x
                or y + h <= row["y"] or row["y"] + row["h"] <= y):
            return True
    return False


def _insert(conn: sqlite3.Connection, values: tuple) -> sqlite3.Row:
    try:
        conn.execute(
            "INSERT INTO dashboard_layout "
            "(id, widget_id, tab_id, x, y, w, h, config_override, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
    except sqlite3.IntegrityError as exc:
        raise conflict(f"Layout-Konflikt: {exc}") from exc
    return conn.execute(
        "SELECT * FROM dashboard_layout WHERE id = ?", (values[0],)
    ).fetchone()


@router.get("")
async def list_layout(tab: str | None = Query(default=None)) -> list[dict]:
    with db.get_conn() as conn:
        if tab is None:
            rows = conn.execute(
                "SELECT * FROM dashboard_layout ORDER BY tab_id, y, x"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM dashboard_layout WHERE tab_id = ? ORDER BY y, x", (tab,)
            ).fetchall()
    return [_instance(row) for row in rows]


@router.post("", status_code=201)
async def create_layout(payload: LayoutCreate) -> dict:
    _check_bounds(payload.x, payload.y, payload.w, payload.h)
    with db.get_conn() as conn:
        if conn.execute("SELECT 1 FROM widgets WHERE id = ?", (payload.widget_id,)).fetchone() is None:
            raise not_found(f"Widget {payload.widget_id} nicht gefunden")
        if conn.execute("SELECT 1 FROM tabs WHERE id = ?", (payload.tab_id,)).fetchone() is None:
            raise not_found(f"Tab {payload.tab_id} nicht gefunden")
        if _overlaps(conn, payload.tab_id, payload.x, payload.y, payload.w, payload.h):
            raise conflict("Layout ueberlappt eine bestehende Instanz")
        now = db.utc_now()
        instance_id = str(uuid.uuid4())
        row = _insert(
            conn,
            (
                instance_id,
                payload.widget_id,
                payload.tab_id,
                payload.x,
                payload.y,
                payload.w,
                payload.h,
                json.dumps(payload.config_override),
                now,
                now,
            ),
        )
    return _instance(row)


@router.put("/{instance_id}")
async def update_layout(instance_id: str, payload: LayoutUpdate) -> dict:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM dashboard_layout WHERE id = ?", (instance_id,)
        ).fetchone()
        if row is None:
            raise not_found(f"Layout-Instanz {instance_id} nicht gefunden")
        x = row["x"] if payload.x is None else payload.x
        y = row["y"] if payload.y is None else payload.y
        w = row["w"] if payload.w is None else payload.w
        h = row["h"] if payload.h is None else payload.h
        _check_bounds(x, y, w, h)
        if _overlaps(conn, row["tab_id"], x, y, w, h, exclude_id=instance_id):
            raise conflict("Layout ueberlappt eine bestehende Instanz")
        config_override = (
            row["config_override"] if payload.config_override is None
            else json.dumps(payload.config_override)
        )
        try:
            conn.execute(
                "UPDATE dashboard_layout SET x = ?, y = ?, w = ?, h = ?, "
                "config_override = ?, updated_at = ? WHERE id = ?",
                (x, y, w, h, config_override, db.utc_now(), instance_id),
            )
        except sqlite3.IntegrityError as exc:
            raise conflict(f"Layout-Konflikt: {exc}") from exc
        row = conn.execute(
            "SELECT * FROM dashboard_layout WHERE id = ?", (instance_id,)
        ).fetchone()
    return _instance(row)


@router.delete("/{instance_id}", status_code=204)
async def delete_layout(instance_id: str) -> None:
    with db.get_conn() as conn:
        cursor = conn.execute("DELETE FROM dashboard_layout WHERE id = ?", (instance_id,))
        if cursor.rowcount == 0:
            raise not_found(f"Layout-Instanz {instance_id} nicht gefunden")
