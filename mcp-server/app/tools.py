"""Tool-Definitionen und -Handler (Spezifikation F.2).

Das ``inputSchema`` ist die oeffentliche API dieses Servers und wird
eins zu eins aus der Spezifikation uebernommen. Jedes Tool liefert
``{ok: true, …}`` oder ``{ok: false, error: {code, message, retryable}}`` —
niemals eine Exception oder einen Stacktrace (F.1).
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from jsonschema import Draft7Validator, FormatChecker
from mcp import types

from .backend_client import BackendClient, BackendError
from .display import DisplayController, DisplayUnavailable

logger = logging.getLogger("worldplaner.mcp")

_ERROR_CODES = (
    "VALIDATION_ERROR",
    "NOT_FOUND",
    "CONFIRMATION_REQUIRED",
    "BACKEND_UNAVAILABLE",
    "DISPLAY_UNAVAILABLE",
    "UNAUTHORIZED",
    "RATE_LIMITED",
    "INTERNAL",
)


def ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


def fail(code: str, message: str, retryable: bool = False, **partial: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "error": {"code": code, "message": message, "retryable": retryable},
    }
    result.update(partial)
    return result


# --- inputSchema (woertlich aus Spezifikation F.2) -------------------------

_EMPTY_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {}}

DASHBOARD_STATUS_SCHEMA = dict(_EMPTY_SCHEMA)

TASK_LIST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {
            "type": "string",
            "enum": ["open", "done", "all"],
            "default": "all",
            "description": "Filter",
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
    },
}

TASK_CREATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title"],
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 200},
        "description": {"type": "string", "default": ""},
        "priority": {
            "type": "string",
            "enum": ["low", "normal", "high"],
            "default": "normal",
        },
        "due_at": {
            "type": "string",
            "format": "date",
            "description": "YYYY-MM-DD, optional",
        },
    },
}

TASK_UPDATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["task_id"],
    "properties": {
        "task_id": {"type": "string", "minLength": 1},
        "title": {"type": "string", "minLength": 1, "maxLength": 200},
        "description": {"type": "string"},
        "status": {"type": "string", "enum": ["open", "done"]},
        "priority": {"type": "string", "enum": ["low", "normal", "high"]},
        "due_at": {"type": "string", "format": "date"},
    },
}

TASK_DELETE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["task_id", "confirm"],
    "properties": {
        "task_id": {"type": "string", "minLength": 1},
        "confirm": {"type": "boolean", "description": "Muss true sein"},
        "expected_title": {
            "type": "string",
            "description": "Optional: aktueller Titel zur Doppelprüfung",
        },
    },
}

SCREEN_OFF_SCHEMA = dict(_EMPTY_SCHEMA)
SCREEN_ON_SCHEMA = dict(_EMPTY_SCHEMA)

SCREEN_DIM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "brightness": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.3,
        }
    },
}

# inputSchema woertlich aus spec-phase2 D/WP-P2-5 (oeffentliche API: unveraendert).
GARMIN_ADD_WORKOUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["activity_type", "start_time", "duration_s"],
    "properties": {
        "activity_type": {"type": "string", "minLength": 1},
        "start_time": {"type": "string", "description": "ISO-8601 UTC"},
        "duration_s": {"type": "integer", "minimum": 1},
        "distance_m": {"type": "number", "minimum": 0},
        "calories": {"type": "integer", "minimum": 0},
        "title": {"type": "string"},
    },
}


# Jede Beschreibung nennt Zweck **und** Grenze (F.1: das Modell kennt den
# Server nicht und liest nur diesen Text).
TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "dashboard_status",
        "description": (
            "Liest den Gesamtzustand des Dashboards (nur lesend, keine Nebenwirkung): "
            "aktiver Tab, Bildschirmzustand (on/off) und Helligkeit, Anzahl offener und "
            "erledigter To-Dos, Backend-Gesundheit. Aendert nichts. Hinweis: "
            "`last_garmin_sync` ist im MVP immer null, weil die Backend-REST-API "
            "(D.5) keinen Sync-Zeitstempel liefert."
        ),
        "inputSchema": DASHBOARD_STATUS_SCHEMA,
        "read_only": True,
    },
    {
        "name": "task_list",
        "description": (
            "Listet Hermes-To-Dos (nur lesend). Wann: um offene oder erledigte "
            "Aufgaben zu lesen. Liefert hoechstens 200 Eintraege (hartes Limit); "
            "`limit` ist auf 1..200 begrenzt."
        ),
        "inputSchema": TASK_LIST_SCHEMA,
        "read_only": True,
    },
    {
        "name": "task_create",
        "description": (
            "Legt eine neue To-Do an (schreibend). Wann: um eine Aufgabe ins "
            "Dashboard zu schreiben. `title` ist erforderlich. Die Aufgabe wird mit "
            "source='hermes' angelegt; der MCP-Server schreibt ausschliesslich ueber "
            "die Backend-REST-API, nie direkt in die Datenbank."
        ),
        "inputSchema": TASK_CREATE_SCHEMA,
    },
    {
        "name": "task_update",
        "description": (
            "Aendert eine bestehende To-Do (schreibend). Wann: um Titel, Beschreibung, "
            "Status, Prioritaet oder Faelligkeit zu ändern. `task_id` ist erforderlich, "
            "zusaetzlich mindestens ein zu aenderndes Feld. Ueberschreibt bedingungslos "
            "(Konfliktregel \"Hermes gewinnt\"); eine unbekannte `task_id` liefert NOT_FOUND."
        ),
        "inputSchema": TASK_UPDATE_SCHEMA,
    },
    {
        "name": "task_delete",
        "description": (
            "Loescht eine To-Do endgueltig (schreibend, destruktiv). Wann: um eine "
            "Aufgabe zu entfernen. Verlangt explizite Bestaetigung: `confirm` muss "
            "`true` sein, sonst wird nichts geloescht und CONFIRMATION_REQUIRED samt "
            "aktueller Aufgabe zurueckgegeben. Weicht `expected_title` vom aktuellen "
            "Titel ab, wird ebenfalls nichts geloescht."
        ),
        "inputSchema": TASK_DELETE_SCHEMA,
        "destructive": True,
    },
    {
        "name": "screen_off",
        "description": (
            "Schaltet das Display vollstaendig aus (schreibend). Wann: wenn das "
            "Display nicht gebraucht wird. Fuehrt das dokumentierte Skript aus "
            "(`xset dpms force off`) und setzt dashboard_state['screen']='off'; bei "
            "fehlendem X11 kommt DISPLAY_UNAVAILABLE statt eines Absturzes."
        ),
        "inputSchema": SCREEN_OFF_SCHEMA,
    },
    {
        "name": "screen_on",
        "description": (
            "Schaltet das Display wieder ein (schreibend). Fuehrt die dokumentierten "
            "Skriptzeilen aus (`xset dpms force on` + `xrandr --brightness 1.0`) und "
            "setzt dashboard_state['screen']='on', brightness=1.0; bei fehlendem X11 "
            "kommt DISPLAY_UNAVAILABLE."
        ),
        "inputSchema": SCREEN_ON_SCHEMA,
    },
    {
        "name": "screen_dim",
        "description": (
            "Dimmt das Display auf eine Helligkeit (schreibend, aggressiv erlaubt). "
            "Wann: nachts oder bei Nichtgebrauch, um Strom zu sparen. `brightness` "
            "liegt zwischen 0.0 und 1.0 (Default 0.3); fuehrt `xrandr --brightness` aus "
            "und setzt dashboard_state['brightness']; bei fehlendem X11 kommt "
            "DISPLAY_UNAVAILABLE."
        ),
        "inputSchema": SCREEN_DIM_SCHEMA,
    },
    {
        "name": "garmin_add_workout",
        "description": (
            "Legt eine abgeschlossene Aktivitaet im Garmin-Account an "
            "(schreibend). Wann: wenn Hermes ein bereits absolviertes Workout "
            "nachtraegt, damit das Dashboard es aus dem Cache zeigt. Pflicht: "
            "`activity_type` (Garmin-Typ, z. B. running), `start_time` "
            "(ISO-8601 UTC) und `duration_s` (>= 1); optional `distance_m`, "
            "`calories` und `title`. Grenze: dies ist das EINZIGE Garmin-"
            "Schreibtool — Bearbeiten/Loeschen bestehender Aktivitaeten wird von "
            "der inoffiziellen Garmin-API nicht zuverlaessig unterstuetzt und "
            "ist nicht Teil dieses Servers (D14). Der MCP-Server schreibt nie "
            "selbst nach Garmin: der Aufruf geht an das Backend, das an "
            "garmin-sync delegiert (D16). Antwort: `{ok:true, workout:{...}}` mit "
            "`activity_id`; Fehler `GARMIN_UNAVAILABLE` (retryable) oder "
            "`GARMIN_AUTH_FAILED` (nicht retryable)."
        ),
        "inputSchema": GARMIN_ADD_WORKOUT_SCHEMA,
    },
]

_UPDATABLE_FIELDS = ("title", "description", "status", "priority", "due_at")


def build_tools() -> list[types.Tool]:
    """Erzeugt die MCP-Toolobjekte mit unveraendertem inputSchema (Draft 07)."""
    tools: list[types.Tool] = []
    for spec in TOOL_DEFS:
        kwargs: dict[str, Any] = {
            "name": spec["name"],
            "description": spec["description"],
            "inputSchema": spec["inputSchema"],
        }
        if spec.get("read_only"):
            kwargs["annotations"] = types.ToolAnnotations(readOnlyHint=True)
        elif spec.get("destructive"):
            kwargs["annotations"] = types.ToolAnnotations(destructiveHint=True)
        tools.append(types.Tool(**kwargs))
    return tools


def _format_validation_errors(errors: list[Any]) -> str:
    parts = []
    for error in errors:
        location = "/".join(str(part) for part in error.path) or "(root)"
        parts.append(f"{location}: {error.message}")
    return "; ".join(parts)


def _as_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


class Tools:
    """Validierung an der Grenze, Ausfuehrung in den Handlern (F.1)."""

    def __init__(self, backend: BackendClient, display: DisplayController) -> None:
        self._backend = backend
        self._display = display
        self._by_name = {spec["name"]: spec for spec in TOOL_DEFS}
        self._validators = {
            spec["name"]: Draft7Validator(
                spec["inputSchema"], format_checker=FormatChecker()
            )
            for spec in TOOL_DEFS
        }
        self._handlers: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = {
            "dashboard_status": self._dashboard_status,
            "task_list": self._task_list,
            "task_create": self._task_create,
            "task_update": self._task_update,
            "task_delete": self._task_delete,
            "screen_off": self._screen_off,
            "screen_on": self._screen_on,
            "screen_dim": self._screen_dim,
            "garmin_add_workout": self._garmin_add_workout,
        }

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        spec = self._by_name.get(name)
        if spec is None or name not in self._handlers:
            return fail("NOT_FOUND", f"Unbekanntes Werkzeug: {name}")
        errors = sorted(
            self._validators[name].iter_errors(arguments), key=lambda e: list(e.path)
        )
        if errors:
            return fail("VALIDATION_ERROR", _format_validation_errors(errors))
        try:
            return await self._handlers[name](arguments)
        except BackendError as exc:
            return fail(exc.code, exc.message, exc.retryable, **exc.partial)
        except DisplayUnavailable as exc:
            return fail("DISPLAY_UNAVAILABLE", str(exc), False)
        except Exception:  # noqa: BLE001 — nie eine Exception als Tool-Ergebnis
            logger.exception("Unerwarteter Fehler im Werkzeug %s", name)
            return fail("INTERNAL", "Interner Fehler", False)

    # --- Handler -----------------------------------------------------------

    async def _dashboard_status(self, _: dict[str, Any]) -> dict[str, Any]:
        config = await self._backend.get_config()
        screen = await self._backend.get_state("screen")
        if not isinstance(screen, str) or not screen:
            screen = "on"
        brightness = _as_float(await self._backend.get_state("brightness"), 1.0)
        open_tasks = await self._backend.list_tasks("open", 200)
        done_tasks = await self._backend.list_tasks("done", 200)
        health = await self._backend.health()
        return ok(
            active_tab=config.get("active_tab"),
            screen=screen,
            brightness=brightness,
            tasks={"open": len(open_tasks), "done": len(done_tasks)},
            last_garmin_sync=None,
            backend=health,
        )

    async def _task_list(self, args: dict[str, Any]) -> dict[str, Any]:
        status = args.get("status", "all")
        limit = args.get("limit", 50)
        tasks = await self._backend.list_tasks(status, limit)
        return ok(tasks=tasks)

    async def _task_create(self, args: dict[str, Any]) -> dict[str, Any]:
        task = await self._backend.create_task(
            title=args["title"],
            description=args.get("description", ""),
            priority=args.get("priority", "normal"),
            due_at=args.get("due_at"),
        )
        return ok(task=task)

    async def _task_update(self, args: dict[str, Any]) -> dict[str, Any]:
        fields = {key: args[key] for key in _UPDATABLE_FIELDS if key in args}
        if not fields:
            return fail(
                "VALIDATION_ERROR",
                "task_update benoetigt mindestens ein Feld aus: "
                + ", ".join(_UPDATABLE_FIELDS),
            )
        task = await self._backend.update_task(args["task_id"], fields)
        return ok(task=task)

    async def _task_delete(self, args: dict[str, Any]) -> dict[str, Any]:
        task_id = args["task_id"]
        task = await self._backend.get_task(task_id)
        if task is None:
            return fail("NOT_FOUND", f"Task {task_id} nicht gefunden")
        if args["confirm"] is not True:
            return fail(
                "CONFIRMATION_REQUIRED",
                "task_delete verlangt confirm=true",
                True,
                task=task,
            )
        expected_title = args.get("expected_title")
        if expected_title is not None and expected_title != task.get("title"):
            return fail(
                "CONFIRMATION_REQUIRED",
                "expected_title weicht vom aktuellen Titel ab",
                True,
                task=task,
            )
        await self._backend.delete_task(task_id)
        return ok(deleted_id=task_id)

    async def _garmin_add_workout(self, args: dict[str, Any]) -> dict[str, Any]:
        workout = await self._backend.add_workout(
            activity_type=args["activity_type"],
            start_time=args["start_time"],
            duration_s=args["duration_s"],
            distance_m=args.get("distance_m"),
            calories=args.get("calories"),
            title=args.get("title"),
        )
        return ok(workout=workout)

    # --- Display -----------------------------------------------------------

    async def _persist_display_state(
        self, values: dict[str, str]
    ) -> str | None:
        """Schreibt Anzeigezustand ins Backend; meldet Teilstand statt Absturz."""
        for key, value in values.items():
            try:
                await self._backend.set_state(key, value)
            except BackendError as exc:
                return f"Anzeigezustand '{key}' nicht gespeichert: {exc.message}"
        return None

    async def _screen_off(self, _: dict[str, Any]) -> dict[str, Any]:
        await self._display.off()
        result = ok()
        warning = await self._persist_display_state({"screen": "off"})
        if warning:
            result["warning"] = warning
        return result

    async def _screen_on(self, _: dict[str, Any]) -> dict[str, Any]:
        await self._display.on()
        result = ok()
        warning = await self._persist_display_state({"screen": "on", "brightness": "1.0"})
        if warning:
            result["warning"] = warning
        return result

    async def _screen_dim(self, args: dict[str, Any]) -> dict[str, Any]:
        brightness = _as_float(args.get("brightness", 0.3), 0.3)
        await self._display.dim(brightness)
        result = ok(brightness=brightness)
        # F.2: screen_dim setzt ausschliesslich dashboard_state['brightness'].
        warning = await self._persist_display_state({"brightness": repr(float(brightness))})
        if warning:
            result["warning"] = warning
        return result
