"""Serverseitiges Widget-Permission-Gate (Spezifikation E.4, WP-P2-6).

Die kanonische Permission->Pfad-Abbildung liegt hier einmalig und ist
clientseitig in `frontend/js/widget-runtime.js` (`PERMISSION_RULES`)
verbindlich gespiegelt.

Vertrauensmodell (D20): Anfragen mit `X-Widget-Id` stammen von einem Widget und
werden gegen dessen Manifest geprueft; Anfragen ohne Header stammen von der
App-Shell oder dem MCP-Server und bleiben erlaubt.
"""

import json

from fastapi import Header

from . import db
from .models import ApiError, forbidden, not_found

# Kanonische Permission -> erlaubte Pfadpraefixe (E.4). Schluessel sind das
# Permission-Enum; die Werte dokumentieren den zugehoerigen Widget-Datenzugriff.
PERMISSION_RULES = {
    "garmin_cache": ("/api/garmin/",),
    "weather": ("/api/weather/",),
    "tasks": ("/api/tasks",),
    "state": ("/api/state/",),
    "api_cache": ("/api/cache/",),
    "baikal": ("/api/baikal/",),
    "grocy": ("/api/grocy/",),
    "mealie": ("/api/mealie/",),
}


def require_permission(permission: str):
    """FastAPI-Dependency: erzwingt `permission` fuer Widget-Aufrufe (E.4).

    Unbekannte Permission beim Routenaufbau -> `ValueError`. Zur Laufzeit:
    unbekannte `X-Widget-Id` -> 404 NOT_FOUND; fehlende Permission -> 403
    FORBIDDEN; nicht lesbares `manifest_json` -> 500 INTERNAL (nie stillschweigend
    erlauben); fehlender Header -> durchlassen.
    """
    if permission not in PERMISSION_RULES:
        raise ValueError(f"Unbekannte Permission: {permission}")

    async def dependency(
        x_widget_id: str | None = Header(default=None, alias="X-Widget-Id"),
    ) -> None:
        if x_widget_id is None:
            return
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT manifest_json FROM widgets WHERE id = ?", (x_widget_id,)
            ).fetchone()
        if row is None:
            raise not_found(f"Widget {x_widget_id} nicht gefunden")
        try:
            permissions = json.loads(row["manifest_json"])["permissions"]
        except (TypeError, KeyError, ValueError) as exc:
            raise ApiError(
                "INTERNAL", f"Manifest von Widget {x_widget_id} nicht lesbar"
            ) from exc
        if not isinstance(permissions, list):
            raise ApiError(
                "INTERNAL",
                f"Manifest von Widget {x_widget_id} hat kein permissions-Array",
            )
        if permission not in permissions:
            raise forbidden(
                f"Widget {x_widget_id} hat keine Permission '{permission}'"
            )

    return dependency
