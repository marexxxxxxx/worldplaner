import asyncio

from app.routers.events import EventBus, bus, event_stream


class FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


def test_bus_publish_and_unsubscribe():
    local_bus = EventBus()
    queue = local_bus.subscribe()
    local_bus.publish("task.created", {"id": "1"})
    assert queue.get_nowait() == ("task.created", {"id": "1"})

    local_bus.unsubscribe(queue)
    local_bus.publish("task.updated", {"id": "1"})
    assert queue.empty()


def test_event_stream_sends_ping_then_published_event():
    async def run() -> str:
        stream = event_stream(FakeRequest())
        assert await stream.__anext__() == ": ping\n\n"
        bus.publish("task.created", {"id": "1"})
        event = await stream.__anext__()
        await stream.aclose()
        return event

    assert asyncio.run(run()) == 'event: task.created\ndata: {"id": "1"}\n\n'


def test_create_task_publishes_event(client):
    queue = bus.subscribe()
    try:
        client.post("/api/tasks", json={"title": "neu"})
        event, payload = queue.get_nowait()
        assert event == "task.created"
        assert payload["title"] == "neu"
    finally:
        bus.unsubscribe(queue)


def test_patch_and_delete_publish_events(client):
    created = client.post("/api/tasks", json={"title": "a"}).json()
    queue = bus.subscribe()
    try:
        client.patch(f"/api/tasks/{created['id']}", json={"status": "done"})
        assert queue.get_nowait()[0] == "task.updated"
        client.delete(f"/api/tasks/{created['id']}")
        assert queue.get_nowait() == ("task.deleted", {"id": created["id"]})
    finally:
        bus.unsubscribe(queue)


# --- Fokus-Ereignisse (spec-focus D.2, WP-F1-1) -----------------------------


def test_focus_write_and_reset_publish_events(client, conn):
    conn.execute(
        "INSERT INTO widgets (id, name, version, manifest_json) VALUES ('w1', 'W', '1.0.0', '{}')"
    )
    client.post(
        "/api/layout",
        json={"widget_id": "w1", "tab_id": "dashboard", "x": 0, "y": 0, "w": 2, "h": 2},
    )
    queue = bus.subscribe()
    try:
        put = client.put("/api/state/focus", json={"widget_id": "w1", "mode": "enlarge"})
        assert put.status_code == 200
        event, payload = queue.get_nowait()
        assert event == "focus.changed"
        assert payload == {"focus": put.json()["focus"]}

        client.delete("/api/state/focus")
        assert queue.get_nowait() == ("focus.changed", {"focus": None})
    finally:
        bus.unsubscribe(queue)


def test_event_stream_delivers_focus_changed():
    async def run() -> str:
        stream = event_stream(FakeRequest())
        assert await stream.__anext__() == ": ping\n\n"
        bus.publish("focus.changed", {"focus": None})
        event = await stream.__anext__()
        await stream.aclose()
        return event

    assert asyncio.run(run()) == 'event: focus.changed\ndata: {"focus": null}\n\n'
