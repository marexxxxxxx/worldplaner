"""Wetter (Spezifikation D.6)."""

from fastapi import APIRouter, Query

from .. import db
from ..services import weather as weather_service

router = APIRouter(prefix="/weather", tags=["weather"])


@router.get("/current")
async def current() -> dict:
    with db.get_conn() as conn:
        return weather_service.current(conn)


@router.get("/forecast")
async def forecast(days: int = Query(default=7, ge=1, le=7)) -> dict:
    with db.get_conn() as conn:
        return weather_service.forecast(conn, days)
