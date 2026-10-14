"""Mealie-Wochenplan (spec-phase2 D/WP-P2-3, Pull-Adapter).

`GET /api/mealie/mealplan?days=7` liefert die normalisierten Mahlzeiten aus
`backend/app/services/mealie.py`. Der serverseitige Permission-Gate (E.4) prueft
Anfragen mit `X-Widget-Id` gegen die Manifest-Permission `mealie`.
"""

from fastapi import APIRouter, Depends, Query

from .. import db
from ..permissions import require_permission
from ..services import mealie as mealie_service

router = APIRouter(prefix="/mealie", tags=["mealie"])


@router.get("/mealplan")
async def mealplan(
    days: int | None = Query(default=None, ge=1, le=7),
    _perm: None = Depends(require_permission("mealie")),
) -> dict:
    """Wochenplan der naechsten `days` Tage (Default aus `MEALIE_DAYS_AHEAD`)."""
    window = days if days is not None else mealie_service.days_ahead()
    with db.get_conn() as conn:
        return mealie_service.mealplan(conn, window)
