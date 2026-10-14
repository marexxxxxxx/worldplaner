"""Garmin-Cache (read-only, Spezifikation D.5/G).

Das Backend liest ausschliesslich aus garmin_cache; geschrieben wird die Tabelle
ausschliesslich von garmin-sync (Single-Writer-Regel, A.2).
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from ..config import get_settings


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def is_stale(fetched_at: str | None) -> bool:
    """stale=true, wenn fetched_at aelter als GARMIN_SYNC_INTERVAL_SECONDS * 2 (D.5)."""
    if fetched_at is None:
        return True
    limit = timedelta(seconds=get_settings().garmin_sync_interval_seconds * 2)
    return datetime.now(timezone.utc) - _parse_ts(fetched_at) > limit


def read_single(conn: sqlite3.Connection, kind: str, date: str) -> tuple[object | None, str | None]:
    """Liest eine Zeile (sleep|readiness|vitals). Liefert (payload, fetched_at)."""
    row = conn.execute(
        "SELECT payload_json, fetched_at FROM garmin_cache WHERE kind = ? AND date = ?",
        (kind, date),
    ).fetchone()
    if row is None:
        return None, None
    return json.loads(row["payload_json"]), row["fetched_at"]


def read_workouts(
    conn: sqlite3.Connection, date: str | None, limit: int
) -> tuple[list, str | None]:
    """Liest Workouts. Ohne date die letzten `limit` ueber alle Daten (D.5)."""
    if date is not None:
        rows = conn.execute(
            "SELECT payload_json, fetched_at FROM garmin_cache "
            "WHERE kind = 'workout' AND date = ? ORDER BY date DESC",
            (date,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT payload_json, fetched_at FROM garmin_cache "
            "WHERE kind = 'workout' ORDER BY date DESC",
        ).fetchall()

    workouts: list = []
    newest: str | None = None
    for row in rows:
        workouts.extend(json.loads(row["payload_json"]))
        if newest is None or row["fetched_at"] > newest:
            newest = row["fetched_at"]
        if len(workouts) >= limit:
            break
    return workouts[:limit], newest
