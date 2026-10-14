"""Widget-Installations-Endpunkte (spec-phase2 D/WP-P2-4).

`POST /api/widgets/install` nimmt ein Zip (multipart-Feld `file`),
`POST /api/widgets/install/git` eine Git-URL entgegen. Beide registrieren das
Widget mit `is_builtin=0` und antworten `201 {widget: {…}}` (Summary-Form von
`GET /api/widgets`). Deinstallation siehe `DELETE /api/widgets/{id}` in
`routers/widgets.py`.

Integrationshinweis (Plan 10.2): `install` muss in `main.py` **vor** `widgets`
in die `include_router`-Schleife aufgenommen werden. Der Pfad
`POST /widgets/install` liegt sonst hinter `GET/PATCH /widgets/{widget_id}` und
antwortet mit `405` statt `201` (Starlette nimmt die erste Pfadübereinstimmung).
"""

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from ..services import install as install_service
from .widgets import _summary

router = APIRouter(prefix="/widgets", tags=["widgets"])


class GitInstallRequest(BaseModel):
    """Payload fuer `POST /api/widgets/install/git` (`ref` Default `main`)."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1)
    ref: str = Field(default="main", min_length=1)


@router.post("/install", status_code=201)
async def install_from_zip(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    row = install_service.install_zip(data)
    return {"widget": _summary(row)}


@router.post("/install/git", status_code=201)
async def install_from_git(payload: GitInstallRequest) -> dict:
    row = install_service.install_git(payload.url, payload.ref)
    return {"widget": _summary(row)}
