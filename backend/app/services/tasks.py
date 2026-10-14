"""Hermes-To-Dos (Spezifikation D.4) inkl. Konfliktregel „Hermes gewinnt"."""

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from ..models import TaskCreate, TaskUpdate, conflict, not_found

FIELDS = [
    "id",
    "title",
    "description",
    "status",
    "priority",
    "source",
    "due_at",
    "completed_at",
    "version",
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _task(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in FIELDS}


def get_task(conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM hermes_tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise not_found(f"Task {task_id} nicht gefunden")
    return _task(row)


def list_tasks(conn: sqlite3.Connection, status: str, limit: int) -> list[dict[str, Any]]:
    sql = "SELECT * FROM hermes_tasks"
    params: list[Any] = []
    if status != "all":
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    return [_task(row) for row in conn.execute(sql, params).fetchall()]


def create_task(conn: sqlite3.Connection, payload: TaskCreate, source: str = "local") -> dict[str, Any]:
    task_id = str(uuid.uuid4())
    now = _now()
    conn.execute(
        "INSERT INTO hermes_tasks "
        "(id, title, description, status, priority, source, version, due_at, created_at, updated_at) "
        "VALUES (?, ?, ?, 'open', ?, ?, 1, ?, ?, ?)",
        (task_id, payload.title, payload.description, payload.priority, source, payload.due_at, now, now),
    )
    return get_task(conn, task_id)


def update_task(conn: sqlite3.Connection, task_id: str, payload: TaskUpdate) -> dict[str, Any]:
    """PATCH /api/tasks/{id}.

    Mit base_version: optimistische Sperre, Abweichung -> 409 (Frontend-Pfad).
    Ohne base_version: bedingungsloses Ueberschreiben, source='hermes' (Hermes-Pfad, D.4).
    """
    row = conn.execute("SELECT * FROM hermes_tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise not_found(f"Task {task_id} nicht gefunden")

    fields = payload.model_dump(exclude_unset=True, exclude={"base_version"})
    hermes = "base_version" not in payload.model_fields_set

    if not hermes and payload.base_version != row["version"]:
        raise conflict("Versionskonflikt")

    now = _now()
    updates: dict[str, Any] = {}
    for key in ("title", "description", "status", "priority", "due_at"):
        if key in fields:
            updates[key] = fields[key]

    if "status" in updates:
        if updates["status"] == "done":
            updates["completed_at"] = now
        else:
            updates["completed_at"] = None

    if hermes:
        updates["source"] = "hermes"

    sets = [f"{key} = ?" for key in updates]
    values = list(updates.values())
    sets.append("version = ?")
    values.append(row["version"] + 1)
    sets.append("updated_at = ?")
    values.append(now)
    values.append(task_id)
    conn.execute(f"UPDATE hermes_tasks SET {', '.join(sets)} WHERE id = ?", values)
    return get_task(conn, task_id)


def delete_task(conn: sqlite3.Connection, task_id: str) -> None:
    if conn.execute("SELECT 1 FROM hermes_tasks WHERE id = ?", (task_id,)).fetchone() is None:
        raise not_found(f"Task {task_id} nicht gefunden")
    conn.execute("DELETE FROM hermes_tasks WHERE id = ?", (task_id,))
