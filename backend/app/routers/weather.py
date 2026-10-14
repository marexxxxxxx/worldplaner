"""Wetter (Spezifikation D.6)."""

from fastapi import APIRouter, Depends, Query

from .. import db
from ..permissions import require_permission
from ..services import weather as weather_service

router = APIRouter(prefix="/weather", tags=["weather"])


@router.get("/current")
async def current(
    _perm: None = Depends(require_permission("weather")),
) -> dict:
    with db.get_conn() as conn:
        return weather_service.current(conn)


@router.get("/forecast")
async def forecast(
    days: int = Query(default=7, ge=1, le=7),
    _perm: None = Depends(require_permission("weather")),
) -> dict:
    with db.get_conn() as conn:
        return weather_service.forecast(conn, days)
