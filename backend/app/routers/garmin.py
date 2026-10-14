"""Garmin (lesend aus garmin_cache, schreibend ueber garmin-sync; D.5/WP-P2-5)."""

from fastapi import APIRouter, Depends, Query

from .. import db
from ..models import GarminWorkoutCreate
from ..permissions import require_permission
from ..services import garmin_cache, garmin_write

_DATE = r"^\d{4}-\d{2}-\d{2}$"

router = APIRouter(prefix="/garmin", tags=["garmin"])


@router.get("/sleep")
async def sleep(
    date: str | None = Query(default=None, pattern=_DATE),
    _perm: None = Depends(require_permission("garmin_cache")),
) -> dict:
    day = date or garmin_cache.today_utc()
    with db.get_conn() as conn:
        payload, fetched_at = garmin_cache.read_single(conn, "sleep", day)
    return {"date": day, "data": payload, "stale": garmin_cache.is_stale(fetched_at)}


@router.get("/readiness")
async def readiness(
    date: str | None = Query(default=None, pattern=_DATE),
    _perm: None = Depends(require_permission("garmin_cache")),
) -> dict:
    day = date or garmin_cache.today_utc()
    with db.get_conn() as conn:
        payload, fetched_at = garmin_cache.read_single(conn, "readiness", day)
    return {"date": day, "data": payload, "stale": garmin_cache.is_stale(fetched_at)}


@router.get("/workouts")
async def workouts(
    limit: int = Query(default=10, ge=1, le=50),
    date: str | None = Query(default=None, pattern=_DATE),
    _perm: None = Depends(require_permission("garmin_cache")),
) -> dict:
    with db.get_conn() as conn:
        items, fetched_at = garmin_cache.read_workouts(conn, date, limit)
    return {"workouts": items, "stale": garmin_cache.is_stale(fetched_at)}


@router.get("/vitals")
async def vitals(
    date: str | None = Query(default=None, pattern=_DATE),
    _perm: None = Depends(require_permission("garmin_cache")),
) -> dict:
    day = date or garmin_cache.today_utc()
    with db.get_conn() as conn:
        payload, fetched_at = garmin_cache.read_single(conn, "vitals", day)
    return {"date": day, "data": payload, "stale": garmin_cache.is_stale(fetched_at)}


@router.post("/workouts", status_code=201)
async def create_workout(
    payload: GarminWorkoutCreate,
    _perm: None = Depends(require_permission("garmin_cache")),
) -> dict:
    """Legt eine Aktivitaet an (schreibend, WP-P2-5).

    Delegiert an `garmin-sync` `POST /write` (D16); das Ergebnis wird dort als
    `kind=workout` in `garmin_cache` re-synct und hier unveraendert
    zurueckgegeben. Fehler kommen als `GARMIN_UNAVAILABLE`/`GARMIN_AUTH_FAILED`
    (502) bzw. `VALIDATION_ERROR` (400).
    """
    workout = await garmin_write.add_workout(payload.model_dump(exclude_none=True))
    return {"workout": workout}
