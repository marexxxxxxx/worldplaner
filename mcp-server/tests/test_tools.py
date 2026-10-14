"""Handler-, Fehler- und Validierungspfade der MCP-Tools (F.1/F.2).

Alle Faelle laufen ohne Netzwerk und ohne X11 gegen Fakes; geprueft wird,
dass kein Pfad eine Exception nach aussen traegt, sondern immer die
Konvention ``{ok:false, error:{code,message,retryable}}`` liefert.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.backend_client import BackendError
from app.display import DisplayUnavailable
from app.tools import Tools


class FakeBackend:
    def __init__(
        self,
        *,
        unavailable: bool = False,
        explode: bool = False,
        garmin_error: tuple[str, bool] | None = None,
    ) -> None:
        self.task: dict[str, Any] = {
            "id": "task-1",
            "title": "Milch kaufen",
            "description": "",
            "status": "open",
            "priority": "normal",
            "source": "hermes",
            "due_at": None,
            "completed_at": None,
            "version": 2,
        }
        self.unavailable = unavailable
        self.explode = explode
        self.garmin_error = garmin_error
        self.deleted: list[str] = []
        self.created: list[dict[str, Any]] = []
        self.workouts: list[dict[str, Any]] = []
        self.states: dict[str, str] = {}
        # Layout wie die Backend-API: Instanz-UUID heisst hier `id`.
        self.layout: list[dict[str, Any]] = [
            {
                "id": "inst-cal",
                "widget_id": "baikal-calendar",
                "tab_id": "dashboard",
                "x": 0,
                "y": 0,
                "w": 4,
                "h": 4,
                "config_override": {},
            },
            {
                "id": "inst-workout",
                "widget_id": "garmin-workout",
                "tab_id": "todos",
                "x": 2,
                "y": 1,
                "w": 6,
                "h": 3,
                "config_override": {},
            },
        ]
        self.focus_calls: list[tuple[str, str, str | None]] = []
        self.focus_cleared = 0

    def _guard(self) -> None:
        if self.explode:
            raise RuntimeError("absichtlicher Testfehler")
        if self.unavailable:
            raise BackendError("BACKEND_UNAVAILABLE", "Backend nicht erreichbar", True)


    async def get_config(self) -> dict[str, Any]:
        self._guard()
        return {"active_tab": "dashboard"}

    async def get_state(self, key: str) -> Any:
        self._guard()
        return self.states.get(key)

    async def list_tasks(self, status: str, limit: int) -> list[dict[str, Any]]:
        self._guard()
        tasks = [self.task]
        if status != "all":
            tasks = [task for task in tasks if task["status"] == status]
        return tasks[:limit]

    async def health(self) -> str:
        self._guard()
        return "ok"

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        self._guard()
        return self.task if task_id == self.task["id"] else None

    async def list_layout(self, tab: str | None = None) -> list[dict[str, Any]]:
        self._guard()
        if tab is None:
            return list(self.layout)
        return [item for item in self.layout if item["tab_id"] == tab]

    async def create_task(self, **kwargs: Any) -> dict[str, Any]:
        self._guard()
        self.created.append(kwargs)
        return {**self.task, "title": kwargs["title"], "source": "hermes"}

    async def update_task(self, task_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        self._guard()
        if task_id != self.task["id"]:
            raise BackendError("NOT_FOUND", f"Task {task_id} nicht gefunden")
        return {**self.task, **fields, "source": "hermes"}

    async def delete_task(self, task_id: str) -> None:
        self._guard()
        self.deleted.append(task_id)

    async def add_workout(self, **kwargs: Any) -> dict[str, Any]:
        self._guard()
        self.workouts.append(kwargs)
        if self.garmin_error is not None:
            code, retryable = self.garmin_error
            raise BackendError(code, "garmin", retryable)
        return {
            "date": "2026-10-14",
            "activity_id": "7000000001",
            "activity_type": kwargs["activity_type"],
            "activity_name": kwargs.get("title"),
            "start_time": kwargs["start_time"],
            "duration_s": kwargs["duration_s"],
            "distance_m": kwargs.get("distance_m"),
            "calories": kwargs.get("calories"),
            "avg_hr": None,
            "max_hr": None,
        }

    async def set_state(self, key: str, value: str) -> None:
        self._guard()
        self.states[key] = value

    async def set_focus(self, widget_id: str, mode: str) -> dict[str, Any]:
        self._guard()
        instance = next(
            (item for item in self.layout if item["widget_id"] == widget_id), None
        )
        if instance is None:
            raise BackendError(
                "NOT_FOUND", f"Widget {widget_id} ist auf keinem Tab platziert"
            )
        focus = {
            "widget_id": widget_id,
            "instance_id": instance["id"],
            "tab_id": instance["tab_id"],
            "mode": mode,
            "ts": "2026-10-14T12:00:00Z",
        }
        self.focus_calls.append((widget_id, mode, instance["id"]))
        self.states["focus"] = focus
        return focus

    async def clear_focus(self) -> None:
        self._guard()
        self.focus_cleared += 1
        self.states["focus"] = None


class FakeDisplay:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.calls: list[Any] = []

    async def off(self) -> None:
        if self.unavailable:
            raise DisplayUnavailable("xset nicht ausführbar oder X11 nicht erreichbar")
        self.calls.append("off")

    async def on(self) -> None:
        if self.unavailable:
            raise DisplayUnavailable("xset nicht ausführbar oder X11 nicht erreichbar")
        self.calls.append("on")

    async def dim(self, brightness: float) -> None:
        if self.unavailable:
            raise DisplayUnavailable("xrandr nicht ausführbar oder X11 nicht erreichbar")
        self.calls.append(("dim", brightness))


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def build(**kwargs: Any) -> Tools:
    backend = FakeBackend(**kwargs)
    display = FakeDisplay(unavailable=kwargs.get("unavailable", False))
    return Tools(backend, display)


def test_invalid_input_is_named():
    tools = build()
    result = run(tools.dispatch("task_create", {}))
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"
    assert "title" in result["error"]["message"]


def test_extra_property_is_rejected():
    tools = build()
    result = run(tools.dispatch("task_create", {"title": "x", "unexpected": 1}))
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_limit_bound_is_enforced():
    tools = build()
    result = run(tools.dispatch("task_list", {"limit": 500}))
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_tool_is_not_found():
    tools = build()
    result = run(tools.dispatch("does_not_exist", {}))
    assert result["error"]["code"] == "NOT_FOUND"


def test_task_delete_requires_confirmation():
    tools = build()
    result = run(tools.dispatch("task_delete", {"task_id": "task-1", "confirm": False}))
    assert result["ok"] is False
    assert result["error"]["code"] == "CONFIRMATION_REQUIRED"
    assert result["error"]["retryable"] is True
    assert result["task"]["id"] == "task-1"
    assert tools._backend.deleted == []


def test_task_delete_expected_title_mismatch_blocks():
    tools = build()
    result = run(
        tools.dispatch(
            "task_delete",
            {"task_id": "task-1", "confirm": True, "expected_title": "anders"},
        )
    )
    assert result["error"]["code"] == "CONFIRMATION_REQUIRED"
    assert tools._backend.deleted == []


def test_task_delete_with_confirmation_deletes():
    tools = build()
    result = run(
        tools.dispatch(
            "task_delete",
            {"task_id": "task-1", "confirm": True, "expected_title": "Milch kaufen"},
        )
    )
    assert result == {"ok": True, "deleted_id": "task-1"}
    assert tools._backend.deleted == ["task-1"]


def test_task_delete_unknown_id_is_not_found():
    tools = build()
    result = run(tools.dispatch("task_delete", {"task_id": "fehlt", "confirm": True}))
    assert result["error"]["code"] == "NOT_FOUND"


def test_task_update_requires_a_field():
    tools = build()
    result = run(tools.dispatch("task_update", {"task_id": "task-1"}))
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_screen_off_without_x11_is_display_unavailable():
    tools = build(unavailable=True)
    result = run(tools.dispatch("screen_off", {}))
    assert result["ok"] is False
    assert result["error"]["code"] == "DISPLAY_UNAVAILABLE"
    assert result["error"]["retryable"] is False


def test_screen_dim_persists_brightness_and_returns_value():
    tools = build()
    result = run(tools.dispatch("screen_dim", {"brightness": 0.2}))
    assert result["ok"] is True
    assert result["brightness"] == 0.2
    assert tools._backend.states["brightness"] == "0.2"
    assert tools._display.calls == [("dim", 0.2)]


def test_screen_on_sets_state():
    tools = build()
    result = run(tools.dispatch("screen_on", {}))
    assert result["ok"] is True
    assert tools._backend.states == {"screen": "on", "brightness": "1.0"}


def test_backend_unavailable_is_reported_not_raised():
    tools = build(unavailable=True)
    result = run(tools.dispatch("task_list", {}))
    assert result["ok"] is False
    assert result["error"]["code"] == "BACKEND_UNAVAILABLE"
    assert result["error"]["retryable"] is True


def test_unexpected_error_becomes_internal_without_stacktrace():
    tools = build(explode=True)
    result = run(tools.dispatch("task_list", {}))
    assert result == {
        "ok": False,
        "error": {"code": "INTERNAL", "message": "Interner Fehler", "retryable": False},
    }


def test_dashboard_status_aggregates():
    tools = build()
    tools._backend.states = {"screen": "off", "brightness": "0.5"}
    result = run(tools.dispatch("dashboard_status", {}))
    assert result["ok"] is True
    assert result["active_tab"] == "dashboard"
    assert result["screen"] == "off"
    assert result["brightness"] == 0.5
    assert result["tasks"] == {"open": 1, "done": 0}
    assert result["backend"] == "ok"
    assert result["last_garmin_sync"] is None
    # spec-focus D.3: focus (null) und tiles des aktiven Tabs.
    assert result["focus"] is None
    assert result["tiles"] == [
        {
            "widget_id": "baikal-calendar",
            "instance_id": "inst-cal",
            "tab_id": "dashboard",
            "x": 0,
            "y": 0,
            "w": 4,
            "h": 4,
        }
    ]


# --- dashboard_focus / dashboard_reset (WP-F1-2, spec-focus D.3) -----------


def test_dashboard_focus_returns_focus_object():
    tools = build()
    result = run(
        tools.dispatch(
            "dashboard_focus", {"widget_id": "baikal-calendar", "mode": "enlarge"}
        )
    )
    assert result["ok"] is True
    assert result["focus"] == {
        "widget_id": "baikal-calendar",
        "instance_id": "inst-cal",
        "tab_id": "dashboard",
        "mode": "enlarge",
        "ts": "2026-10-14T12:00:00Z",
    }
    assert tools._backend.focus_calls == [("baikal-calendar", "enlarge", "inst-cal")]


def test_dashboard_focus_unknown_widget_is_not_found():
    tools = build()
    result = run(
        tools.dispatch("dashboard_focus", {"widget_id": "gibt-es-nicht", "mode": "highlight"})
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"
    assert tools._backend.focus_calls == []


def test_dashboard_focus_missing_mode_is_validation_error():
    tools = build()
    result = run(tools.dispatch("dashboard_focus", {"widget_id": "baikal-calendar"}))
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"
    assert "mode" in result["error"]["message"]
    assert tools._backend.states.get("focus") is None


def test_dashboard_focus_rejects_unknown_mode():
    tools = build()
    result = run(
        tools.dispatch(
            "dashboard_focus", {"widget_id": "baikal-calendar", "mode": "zoom"}
        )
    )
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_dashboard_focus_rejects_extra_field():
    tools = build()
    result = run(
        tools.dispatch(
            "dashboard_focus",
            {"widget_id": "baikal-calendar", "mode": "enlarge", "unexpected": 1},
        )
    )
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_dashboard_focus_backend_unavailable_is_reported_not_raised():
    tools = build(unavailable=True)
    result = run(
        tools.dispatch(
            "dashboard_focus", {"widget_id": "baikal-calendar", "mode": "enlarge"}
        )
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "BACKEND_UNAVAILABLE"
    assert result["error"]["retryable"] is True


def test_dashboard_reset_returns_null_focus():
    tools = build()
    result = run(tools.dispatch("dashboard_reset", {}))
    assert result == {"ok": True, "focus": None}
    assert tools._backend.focus_cleared == 1


def test_dashboard_reset_rejects_extra_field():
    tools = build()
    result = run(tools.dispatch("dashboard_reset", {"widget_id": "x"}))
    assert result["error"]["code"] == "VALIDATION_ERROR"
    assert tools._backend.focus_cleared == 0


def test_dashboard_reset_backend_unavailable_is_reported_not_raised():
    tools = build(unavailable=True)
    result = run(tools.dispatch("dashboard_reset", {}))
    assert result["ok"] is False
    assert result["error"]["code"] == "BACKEND_UNAVAILABLE"


def test_dashboard_status_reports_active_focus_and_tiles():
    tools = build()
    run(
        tools.dispatch(
            "dashboard_focus", {"widget_id": "garmin-workout", "mode": "fullscreen"}
        )
    )
    result = run(tools.dispatch("dashboard_status", {}))
    assert result["ok"] is True
    assert result["focus"]["widget_id"] == "garmin-workout"
    assert result["focus"]["mode"] == "fullscreen"
    # tiles zeigt weiterhin den aktiven Tab aus der Config ("dashboard").
    assert [tile["widget_id"] for tile in result["tiles"]] == ["baikal-calendar"]


# --- garmin_add_workout (WP-P2-5) ------------------------------------------


def _workout_args(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "activity_type": "running",
        "start_time": "2026-10-14T07:15:00Z",
        "duration_s": 2412,
    }
    args.update(overrides)
    return args


def test_garmin_add_workout_returns_activity_id():
    tools = build()
    result = run(
        tools.dispatch(
            "garmin_add_workout",
            _workout_args(
                distance_m=5210.5, calories=412, title="Morning Run"
            ),
        )
    )
    assert result["ok"] is True
    assert result["workout"]["activity_id"] == "7000000001"
    assert tools._backend.workouts == [
        {
            "activity_type": "running",
            "start_time": "2026-10-14T07:15:00Z",
            "duration_s": 2412,
            "distance_m": 5210.5,
            "calories": 412,
            "title": "Morning Run",
        }
    ]


def test_garmin_add_workout_requires_duration():
    tools = build()
    result = run(
        tools.dispatch(
            "garmin_add_workout",
            {"activity_type": "running", "start_time": "2026-10-14T07:15:00Z"},
        )
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"
    assert "duration_s" in result["error"]["message"]
    assert tools._backend.workouts == []


def test_garmin_add_workout_rejects_extra_field():
    tools = build()
    result = run(tools.dispatch("garmin_add_workout", _workout_args(unexpected=1)))
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_garmin_unavailable_is_passed_through_not_internal():
    tools = build(garmin_error=("GARMIN_UNAVAILABLE", True))
    result = run(tools.dispatch("garmin_add_workout", _workout_args()))
    assert result["ok"] is False
    assert result["error"]["code"] == "GARMIN_UNAVAILABLE"
    assert result["error"]["retryable"] is True


def test_garmin_auth_failed_is_passed_through_not_retryable():
    tools = build(garmin_error=("GARMIN_AUTH_FAILED", False))
    result = run(tools.dispatch("garmin_add_workout", _workout_args()))
    assert result["ok"] is False
    assert result["error"]["code"] == "GARMIN_AUTH_FAILED"
    assert result["error"]["retryable"] is False
