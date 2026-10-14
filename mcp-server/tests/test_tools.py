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
    def __init__(self, *, unavailable: bool = False, explode: bool = False) -> None:
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
        self.deleted: list[str] = []
        self.created: list[dict[str, Any]] = []
        self.states: dict[str, str] = {}

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

    async def set_state(self, key: str, value: str) -> None:
        self._guard()
        self.states[key] = value


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
