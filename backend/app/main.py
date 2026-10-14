"""App-Factory, Lifespan (DB-Init/Schema), Route-Mount, Static-Auslieferung
(Spezifikation A.4 / B / D)."""

import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db
from .config import get_settings
from .models import ApiError
from .routers import events, garmin, layout, state, tasks, weather, widgets

STATUS_TO_CODE = {
    400: "VALIDATION_ERROR",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
}


def error_body(code: str, message: str, retryable: bool = False) -> dict:
    return {"error": {"code": code, "message": message, "retryable": retryable}}


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content=error_body(exc.code, exc.message, exc.retryable),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        message = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        return JSONResponse(status_code=400, content=error_body("VALIDATION_ERROR", message))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = STATUS_TO_CODE.get(exc.status_code, "INTERNAL")
        return JSONResponse(status_code=exc.status_code, content=error_body(code, str(exc.detail)))

    @app.exception_handler(Exception)
    async def _internal(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content=error_body("INTERNAL", "Interner Fehler"))


def create_app() -> FastAPI:
    app = FastAPI(title="worldplaner backend", version="0.1.0", lifespan=lifespan)
    _register_error_handlers(app)

    @app.get("/api/health")
    async def health() -> JSONResponse:
        try:
            db.ping()
        except (sqlite3.Error, OSError):
            return JSONResponse(
                status_code=500, content={"status": "degraded", "db": "unavailable"}
            )
        return JSONResponse(status_code=200, content={"status": "ok", "db": "ok"})

    @app.get("/api/config")
    async def bootstrap() -> dict:
        settings = get_settings()
        with db.get_conn() as conn:
            tab_rows = conn.execute("SELECT * FROM tabs ORDER BY position").fetchall()
            state_row = conn.execute(
                "SELECT value FROM dashboard_state WHERE key = 'active_tab'"
            ).fetchone()
        active_tab = json.loads(state_row["value"]) if state_row is not None else "dashboard"
        return {
            "grid": {
                "columns": settings.grid_columns,
                "row_height_px": settings.grid_row_height_px,
                "gap_px": settings.grid_gap_px,
            },
            "tabs": [
                {
                    "id": row["id"],
                    "slug": row["slug"],
                    "title": row["title"],
                    "position": row["position"],
                    "is_active": bool(row["is_active"]),
                }
                for row in tab_rows
            ],
            "active_tab": active_tab,
        }

    for module in (widgets, layout, tasks, garmin, weather, state, events):
        app.include_router(module.router, prefix="/api")

    _mount_static(app)
    return app


def _mount_static(app: FastAPI) -> None:
    """Frontend/Widgets werden vom Backend ausgeliefert (D.10), sobald vorhanden.

    In diesem Work-Package existieren die Verzeichnisse noch nicht; der Mount
    greift, sobald WP-3/WP-4/WP-6 sie anlegen.
    """
    project_root = Path(__file__).resolve().parent.parent.parent
    widgets_dir = project_root / "widgets"
    frontend_dir = project_root / "frontend"
    if widgets_dir.is_dir():
        app.mount("/widgets", StaticFiles(directory=str(widgets_dir)), name="widgets")
    if frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


app = create_app()
