"""Grocy-Bestand (spec-phase2 D/WP-P2-2, Pull-Adapter)."""

from fastapi import APIRouter, Depends

from .. import db
from ..permissions import require_permission
from ..services import grocy as grocy_service

router = APIRouter(prefix="/grocy", tags=["grocy"])


@router.get("/stock")
async def stock(
    _perm: None = Depends(require_permission("grocy")),
) -> dict:
    with db.get_conn() as conn:
        return grocy_service.stock(conn)
