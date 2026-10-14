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
