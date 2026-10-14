"""SSE-Ereignisstrom (Spezifikation D.8)."""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

HEARTBEAT_SECONDS = 15


class EventBus:
    """In-Prozess-Verteiler: Schreibrouten publizieren, /api/events konsumiert."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: str, payload: dict) -> None:
        for queue in list(self._subscribers):
            queue.put_nowait((event, payload))


bus = EventBus()


async def event_stream(request: Request) -> AsyncIterator[str]:
    queue = bus.subscribe()
    try:
        # Sofortiges Lebenszeichen, damit der Client die Verbindung als offen sieht.
        yield ": ping\n\n"
        while not await request.is_disconnected():
            try:
                event, payload = await asyncio.wait_for(
                    queue.get(), timeout=HEARTBEAT_SECONDS
                )
            except TimeoutError:
                yield ": ping\n\n"
                continue
            yield f"event: {event}\ndata: {json.dumps(payload)}\n\n"
    finally:
        bus.unsubscribe(queue)


router = APIRouter(tags=["events"])


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    return StreamingResponse(event_stream(request), media_type="text/event-stream")
