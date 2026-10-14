"""Baikal-Kalender (spec-phase2 D/WP-P2-1, Pull-Adapter)."""

from fastapi import APIRouter, Depends, Query

from .. import db
from ..permissions import require_permission
from ..services import baikal as baikal_service

router = APIRouter(prefix="/baikal", tags=["baikal"])


@router.get("/events")
async def events(
    days: int | None = Query(default=None, ge=1, le=31),
    _perm: None = Depends(require_permission("baikal")),
) -> dict:
    """Kommende Kalenderereignisse.

    `days` (1–31) steuert den Zeitraum; ohne Angabe gilt `BAIKAL_DAYS_AHEAD`.
    Ein nicht erreichbarer Provider liefert `200` mit `error_code`
    `BAIKAL_UNAVAILABLE` (bzw. den letzten Cache-Wert) statt eines 5xx.
    """
    with db.get_conn() as conn:
        return baikal_service.events(conn, days)
