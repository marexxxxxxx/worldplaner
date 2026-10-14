"""Interner Schreib-Endpunkt von `garmin-sync` (WP-P2-5, `spec-phase2` D/WP-P2-5).

`POST /write` ist der **einzige** Weg, ueber den eine Aktivitaet angelegt wird.
Der Endpunkt laeuft nur im Compose-Netz (kein Host-Port) und wird ausschliesslich
vom Backend (`POST /api/garmin/workouts`) aufgerufen. `garmin-sync` bleibt
einziger Inhaber der Garmin-Session/Token und einziger Schreiber von
`garmin_cache` (Entscheidung D16); der MCP-Server schreibt nie selbst.

Ablauf eines Schreibvorgangs:
1. Payload an der Grenze validieren (``WriteValidationError`` -> 400).
2. Aktivitaet ueber den Garmin-Client anlegen.
3. `workout` sofort in `garmin_cache` re-syncen (G.5).

Fehlerform wie im restlichen System:
``{"ok": false, "error": {"code", "message", "retryable"}}``.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import sync as gs

log = logging.getLogger("garmin-sync.write")

OPERATION_ADD_WORKOUT = "add_workout"

# Internes Compose-Ziel (spec-phase2 D/WP-P2-5: Port 8002, kein Host-Port).
DEFAULT_WRITE_HOST = "0.0.0.0"
DEFAULT_WRITE_PORT = 8002


def error_body(code: str, message: str, retryable: bool) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "retryable": retryable}}


def _activity_id(raw: Any) -> str | None:
    """Aktivitaets-ID aus der Garmin-Antwort ziehen (Feldname variiert)."""
    if isinstance(raw, dict):
        for key in ("activityId", "activity_id", "activityID"):
            value = raw.get(key)
            if value is not None:
                return str(value)
    return None


def _find_workout(
    grouped: dict[str, list[dict[str, Any]]], activity_id: str | None
) -> dict[str, Any] | None:
    if activity_id is None:
        return None
    for items in grouped.values():
        for item in items:
            if item.get("activity_id") == activity_id:
                return item
    return None


class WriteService:
    """Haelt den angemeldeten Garmin-Client und legt eine Aktivitaet an (D16).

    Der Client wird beim ersten Schreibvorgang angemeldet und danach
    wiederverwendet; ein Auth-/Transportfehler verwirft ihn, damit der naechste
    Aufruf eine frische Anmeldung versucht. Jeder Schreibvorgang nutzt eine
    eigene SQLite-Verbindung (der Server laeuft in einem eigenen Thread).
    """

    def __init__(self, config: gs.Config, client_factory: Callable[[gs.Config], Any]) -> None:
        self._config = config
        self._factory = client_factory
        self._client: Any = None
        self._lock = threading.Lock()

    def _client_or_login(self) -> Any:
        with self._lock:
            if self._client is None:
                client = self._factory(self._config)
                client.login()
                self._client = client
            return self._client

    def _reset_client(self) -> None:
        with self._lock:
            self._client = None

    def add_workout(self, payload: Any) -> dict[str, Any]:
        """Validieren, anlegen, re-syncen und den Workout-Eintrag liefern."""
        normalized = gs.validate_workout_payload(payload)
        try:
            client = self._client_or_login()
            raw = client.add_workout(normalized)
            activity_id = _activity_id(raw)
            conn = gs.connect_db(self._config.db_path)
            try:
                grouped = gs.sync_workouts_once(conn, client)
            finally:
                conn.close()
        except (gs.AuthError, gs.TransientError):
            self._reset_client()
            raise
        return _find_workout(grouped, activity_id) or gs.workout_item_from_payload(
            normalized, activity_id
        )


def create_write_app(service: WriteService) -> Starlette:
    """ASGI-App mit `POST /write` und `GET /healthz`."""

    async def write(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - ungueltiger Body ist ein Nein, kein Crash
            return JSONResponse(
                error_body("VALIDATION_ERROR", "Ungueltiger JSON-Body", False),
                status_code=400,
            )
        if not isinstance(body, dict) or body.get("operation") != OPERATION_ADD_WORKOUT:
            return JSONResponse(
                error_body(
                    "VALIDATION_ERROR",
                    f"operation muss '{OPERATION_ADD_WORKOUT}' sein",
                    False,
                ),
                status_code=400,
            )
        try:
            workout = service.add_workout(body.get("payload"))
        except gs.WriteValidationError as exc:
            return JSONResponse(
                error_body("VALIDATION_ERROR", str(exc), False), status_code=400
            )
        except gs.AuthError as exc:
            # Detail nur ins Log, nicht in die Antwort (keine Geheimnisse).
            log.error("Garmin-Authentifizierung fehlgeschlagen: %s", exc)
            return JSONResponse(
                error_body(
                    "GARMIN_AUTH_FAILED",
                    "Garmin-Session ungueltig oder abgelaufen",
                    False,
                ),
                status_code=502,
            )
        except gs.TransientError as exc:
            log.warning("Garmin nicht erreichbar: %s", exc)
            return JSONResponse(
                error_body("GARMIN_UNAVAILABLE", "Garmin nicht erreichbar", True),
                status_code=502,
            )
        return JSONResponse({"ok": True, "workout": workout}, status_code=200)

    async def healthz(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return Starlette(
        routes=[
            Route("/write", write, methods=["POST"]),
            Route("/healthz", healthz, methods=["GET"]),
        ]
    )


def start_write_server(
    config: gs.Config,
    client_factory: Callable[[gs.Config], Any],
    *,
    host: str | None = None,
    port: int | None = None,
) -> threading.Thread:
    """Startet den Write-Server als Daemon-Thread (Aufruf aus `sync.main`).

    `host`/`port` kommen aus `GARMIN_WRITE_HOST`/`GARMIN_WRITE_PORT` (Default
    0.0.0.0:8002). Uvicorn installiert in einem Nicht-Hauptthread keine
    Signal-Handler; der Server laeuft daher ohne Signalsteuerung nebenher.
    """
    import uvicorn

    host = host or os.getenv("GARMIN_WRITE_HOST") or DEFAULT_WRITE_HOST
    port = port if port is not None else int(
        os.getenv("GARMIN_WRITE_PORT") or DEFAULT_WRITE_PORT
    )
    app = create_write_app(WriteService(config, client_factory))
    thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": host, "port": port, "log_level": "warning"},
        name="garmin-write",
        daemon=True,
    )
    thread.start()
    log.info("Write-Server gestartet auf %s:%s (nur Compose-Netz).", host, port)
    return thread
