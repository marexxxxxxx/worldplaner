"""Tests fuer den Garmin-Schreibpfad des Backends (WP-P2-5).

Deckt den Forward-Client zu `garmin-sync` (`POST /write`) samt Fehlercode-
Abbildung (D17) und den Endpunkt `POST /api/garmin/workouts` ab.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.models import ApiError
from app.services import garmin_write

WORKOUT = {
    "date": "2026-10-14",
    "activity_id": "7000000001",
    "activity_type": "running",
    "activity_name": "Morning Run",
    "start_time": "2026-10-14T07:15:00Z",
    "duration_s": 2412,
    "distance_m": 5210.5,
    "calories": 412,
    "avg_hr": None,
    "max_hr": None,
}

PAYLOAD = {
    "activity_type": "running",
    "start_time": "2026-10-14T07:15:00Z",
    "duration_s": 2412,
    "distance_m": 5210.5,
    "calories": 412,
    "title": "Morning Run",
}


def _call(handler):
    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://garmin-sync:8002"
        ) as client:
            return await garmin_write.add_workout(PAYLOAD, client=client)

    return asyncio.run(run())


# --- Forward-Client: Erfolg und Fehlercode-Abbildung (D17) -------------------


def test_add_workout_returns_workout_on_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/write"
        assert json.loads(request.content)["operation"] == "add_workout"
        assert json.loads(request.content)["payload"]["duration_s"] == 2412
        return httpx.Response(200, json={"ok": True, "workout": WORKOUT})

    assert _call(handler) == WORKOUT


def test_add_workout_maps_auth_failed_to_502_not_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            json={
                "ok": False,
                "error": {
                    "code": "GARMIN_AUTH_FAILED",
                    "message": "auth",
                    "retryable": False,
                },
            },
        )

    with pytest.raises(ApiError) as exc:
        _call(handler)
    assert exc.value.code == "GARMIN_AUTH_FAILED"
    assert exc.value.status == 502
    assert exc.value.retryable is False


def test_add_workout_maps_unavailable_to_502_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            json={
                "ok": False,
                "error": {
                    "code": "GARMIN_UNAVAILABLE",
                    "message": "down",
                    "retryable": True,
                },
            },
        )

    with pytest.raises(ApiError) as exc:
        _call(handler)
    assert exc.value.code == "GARMIN_UNAVAILABLE"
    assert exc.value.status == 502
    assert exc.value.retryable is True


def test_add_workout_maps_validation_error_to_400():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "ok": False,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "duration_s fehlt",
                    "retryable": False,
                },
            },
        )

    with pytest.raises(ApiError) as exc:
        _call(handler)
    assert exc.value.code == "VALIDATION_ERROR"
    assert exc.value.status == 400


def test_add_workout_transport_error_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(ApiError) as exc:
        _call(handler)
    assert exc.value.code == "GARMIN_UNAVAILABLE"
    assert exc.value.retryable is True


def test_add_workout_invalid_body_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<html>nope</html>", headers={"content-type": "text/html"}
        )

    with pytest.raises(ApiError) as exc:
        _call(handler)
    assert exc.value.code == "GARMIN_UNAVAILABLE"


# --- Endpunkt POST /api/garmin/workouts --------------------------------------


def test_create_workout_returns_201(client, monkeypatch):
    async def fake(payload):
        assert payload == PAYLOAD
        return WORKOUT

    monkeypatch.setattr(garmin_write, "add_workout", fake)
    response = client.post("/api/garmin/workouts", json=PAYLOAD)
    assert response.status_code == 201
    assert response.json() == {"workout": WORKOUT}


def test_create_workout_missing_duration_is_400(client):
    bad = {"activity_type": "running", "start_time": "2026-10-14T07:15:00Z"}
    response = client.post("/api/garmin/workouts", json=bad)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_workout_extra_field_is_400(client):
    response = client.post("/api/garmin/workouts", json=dict(PAYLOAD, extra=1))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_workout_invalid_start_time_is_400(client):
    response = client.post(
        "/api/garmin/workouts", json=dict(PAYLOAD, start_time="14.10.2026")
    )
    assert response.status_code == 400


def test_create_workout_garmin_unavailable_is_502(client, monkeypatch):
    async def fake(payload):
        raise ApiError(
            "GARMIN_UNAVAILABLE", "garmin-sync nicht erreichbar", retryable=True
        )

    monkeypatch.setattr(garmin_write, "add_workout", fake)
    response = client.post("/api/garmin/workouts", json=PAYLOAD)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "GARMIN_UNAVAILABLE"
    assert response.json()["error"]["retryable"] is True


def test_create_workout_auth_failed_is_502_not_retryable(client, monkeypatch):
    async def fake(payload):
        raise ApiError("GARMIN_AUTH_FAILED", "Session abgelaufen", retryable=False)

    monkeypatch.setattr(garmin_write, "add_workout", fake)
    response = client.post("/api/garmin/workouts", json=PAYLOAD)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "GARMIN_AUTH_FAILED"
    assert response.json()["error"]["retryable"] is False
