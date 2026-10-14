"""Tests fuer den internen Schreibpfad von `garmin-sync` (WP-P2-5).

Belegt die binaeren Abnahmefaelle 1-3:
1. `POST /write` legt eine Aktivitaet an; die Antwort enthaelt eine `activity_id`.
2. Danach enthaelt `garmin_cache` die aktualisierte `workout`-Zeile (Re-Sync).
3. Mit dem Mock-Client laeuft der komplette Pfad ohne echten Garmin-Account.
Zusaetzlich: Validierung an der Grenze und die Auth-/Verfuegbarkeitsfehler.
"""

from __future__ import annotations

import json
import socket
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
import sync as gs
import write_server as ws
from starlette.testclient import TestClient

DAY = "2026-10-14"

WORKOUT_KEYS = {
    "date",
    "activity_id",
    "activity_type",
    "activity_name",
    "start_time",
    "duration_s",
    "distance_m",
    "calories",
    "avg_hr",
    "max_hr",
}

PAYLOAD = {
    "activity_type": "running",
    "start_time": f"{DAY}T07:15:00Z",
    "duration_s": 2412,
    "distance_m": 5210.5,
    "calories": 412,
    "title": "Morning Run",
}


class FakeWriterClient:
    """Minimaler Garmin-Client fuer den Schreibpfad (Abnahmefaelle 1-3)."""

    def __init__(self, *, auth: bool = False, transient: bool = False) -> None:
        self.calls: list[tuple] = []
        self._auth = auth
        self._transient = transient
        self._activities: list[dict] = []
        self._next = 7000000000

    def login(self) -> None:
        self.calls.append(("login",))
        if self._auth:
            raise gs.AuthError("401 Unauthorized")

    def add_workout(self, payload: dict) -> dict:
        self.calls.append(("add_workout", payload))
        if self._auth:
            raise gs.AuthError("401 Unauthorized")
        if self._transient:
            raise gs.TransientError("503 Service Unavailable")
        self._next += 1
        activity = {
            "activityId": self._next,
            "activityType": {"typeKey": payload["activity_type"]},
            "activityName": payload.get("title"),
            "startTimeGMT": payload["start_time"][:19].replace("T", " "),
            "duration": float(payload["duration_s"]),
            "distance": payload.get("distance_m"),
            "calories": payload.get("calories"),
            "averageHR": None,
            "maxHR": None,
        }
        self._activities.insert(0, activity)
        return activity

    def get_activities(self, start: int, limit: int) -> list[dict]:
        self.calls.append(("get_activities", start, limit))
        return list(self._activities[:limit])


def _config(db_path: str, timezone_name: str = "UTC") -> gs.Config:
    return gs.Config(
        email="user@example.com",
        password="secret",
        sync_interval_seconds=1200,
        backoff_base_seconds=60,
        backoff_max_attempts=5,
        backoff_cap_seconds=3600,
        token_dir="/data/token",
        db_path=db_path,
        timezone=timezone_name,
    )


def _rows(db_path: str) -> list[tuple]:
    conn = gs.connect_db(db_path)
    try:
        return [
            (row["kind"], row["date"], json.loads(row["payload_json"]))
            for row in conn.execute(
                "SELECT kind, date, payload_json FROM garmin_cache ORDER BY kind, date"
            )
        ]
    finally:
        conn.close()


def _service(db_path: str, factory) -> ws.WriteService:
    return ws.WriteService(_config(db_path), factory)


def _http_client(db_path: str, factory) -> TestClient:
    return TestClient(ws.create_write_app(_service(db_path, factory)))


class RecordingGarmin:
    """Minimale Fassade des python-garminconnect-Objekts (nur der Schreibaufruf).

    Zeichnet die Argumente von `create_manual_activity` auf, damit der reale
    `RealGarminClient.add_workout`-Pfad ohne Netzwerk und Garmin-Account pruefbar
    ist (Systemgrenze gemockt, nicht die eigene Logik).
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def create_manual_activity(self, *args) -> dict:
        self.calls.append(args)
        return {"activityId": 424242}

    def get_activities(self, start: int, limit: int) -> list:
        return []


def _real_service(db_path: str, timezone_name: str, backend, monkeypatch) -> ws.WriteService:
    """WriteService auf dem echten `RealGarminClient` mit injizierter Fassade."""
    monkeypatch.setattr(gs.RealGarminClient, "login", lambda self: None)

    def factory(config: gs.Config) -> gs.RealGarminClient:
        client = gs.RealGarminClient(config)
        client._garmin = backend
        return client

    return ws.WriteService(_config(db_path, timezone_name), factory)


# --- Abnahmefall 1+2: Anlegen ueber die Service-Schicht ----------------------


def test_add_workout_returns_activity_id_and_updates_cache(db_path):
    client = FakeWriterClient()
    workout = _service(db_path, lambda _: client).add_workout(dict(PAYLOAD))

    assert workout["activity_id"] == "7000000001"
    assert workout["activity_type"] == "running"
    assert workout["date"] == DAY
    assert workout["duration_s"] == 2412
    assert workout["distance_m"] == 5210.5
    assert workout["calories"] == 412
    assert set(workout) == WORKOUT_KEYS

    # Abnahmefall 2: garmin_cache enthaelt die aktualisierte workout-Zeile.
    rows = _rows(db_path)
    assert [(kind, date) for kind, date, _ in rows] == [("workout", DAY)]
    assert [item["activity_id"] for item in rows[0][2]] == ["7000000001"]
    # Re-Sync heisst: die Quelle wird erneut gelesen.
    assert ("get_activities", 0, gs.WORKOUT_LIMIT) in client.calls


def test_add_workout_reuses_logged_in_client(db_path):
    client = FakeWriterClient()
    service = _service(db_path, lambda _: client)
    service.add_workout(dict(PAYLOAD))
    service.add_workout(dict(PAYLOAD))
    assert [call for call in client.calls if call == ("login",)] == [("login",)]


# --- Validierung an der Grenze ----------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"activity_type": "", "start_time": f"{DAY}T07:15:00Z", "duration_s": 1},
        {"activity_type": "running", "duration_s": 1},
        {"activity_type": "running", "start_time": "kein-datum", "duration_s": 1},
        {"activity_type": "running", "start_time": f"{DAY}T07:15:00Z", "duration_s": 0},
        {
            "activity_type": "running",
            "start_time": f"{DAY}T07:15:00Z",
            "duration_s": 1,
            "distance_m": -1,
        },
        {
            "activity_type": "running",
            "start_time": f"{DAY}T07:15:00Z",
            "duration_s": 1,
            "calories": -1,
        },
        {
            "activity_type": "running",
            "start_time": f"{DAY}T07:15:00Z",
            "duration_s": 1,
            "titel": "Tippfehler",
        },
    ],
)
def test_validate_workout_payload_rejects_invalid(db_path, payload):
    service = _service(db_path, lambda _: FakeWriterClient())
    with pytest.raises(gs.WriteValidationError):
        service.add_workout(payload)
    assert _rows(db_path) == []  # nichts geschrieben


def test_validate_workout_payload_normalizes_optional_fields():
    normalized = gs.validate_workout_payload(dict(PAYLOAD))
    assert normalized["start_time"] == f"{DAY}T07:15:00Z"
    assert normalized["distance_m"] == 5210.5
    assert normalized["calories"] == 412


def test_validate_accepts_offset_start_time():
    normalized = gs.validate_workout_payload(
        {
            "activity_type": "cycling",
            "start_time": f"{DAY}T09:15:00+02:00",
            "duration_s": 60,
        }
    )
    assert normalized["start_time"] == f"{DAY}T07:15:00Z"


# --- Abnahmefall 3: kompletter Pfad ueber HTTP --------------------------------


def test_write_endpoint_creates_activity_and_updates_cache(db_path):
    client = _http_client(db_path, lambda _: FakeWriterClient())
    response = client.post(
        "/write", json={"operation": "add_workout", "payload": dict(PAYLOAD)}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["workout"]["activity_id"] == "7000000001"
    assert _rows(db_path)[0][0] == "workout"


def test_write_endpoint_healthz(db_path):
    client = _http_client(db_path, lambda _: FakeWriterClient())
    assert client.get("/healthz").json() == {"status": "ok"}


def test_write_endpoint_rejects_unknown_operation(db_path):
    client = _http_client(db_path, lambda _: FakeWriterClient())
    response = client.post("/write", json={"operation": "delete_workout", "payload": {}})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_write_endpoint_rejects_invalid_json(db_path):
    client = _http_client(db_path, lambda _: FakeWriterClient())
    response = client.post(
        "/write", content=b"not json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_write_endpoint_validation_error(db_path):
    client = _http_client(db_path, lambda _: FakeWriterClient())
    response = client.post("/write", json={"operation": "add_workout", "payload": {}})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert _rows(db_path) == []


def test_write_endpoint_auth_failed_is_not_retryable(db_path):
    client = _http_client(db_path, lambda _: FakeWriterClient(auth=True))
    response = client.post(
        "/write", json={"operation": "add_workout", "payload": dict(PAYLOAD)}
    )
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "GARMIN_AUTH_FAILED"
    assert error["retryable"] is False


def test_write_endpoint_unavailable_is_retryable(db_path):
    client = _http_client(db_path, lambda _: FakeWriterClient(transient=True))
    response = client.post(
        "/write", json={"operation": "add_workout", "payload": dict(PAYLOAD)}
    )
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "GARMIN_UNAVAILABLE"
    assert error["retryable"] is True


# --- Nachtrag MARAAA-43: Zeitzone des Schreibpfads ----------------------------
#
# Abnahmefall 1: bei `GARMIN_TIMEZONE != "UTC"` muss die an Garmin uebergebene
# Startzeit der uebergebene UTC-Zeitpunkt sein — als lokale Wandzeit plus
# Zeitzonen-Schluessel, ohne doppelten Offset.


def test_write_uses_configured_timezone_without_double_offset(db_path, monkeypatch):
    backend = RecordingGarmin()
    client = TestClient(
        ws.create_write_app(_real_service(db_path, "Europe/Berlin", backend, monkeypatch))
    )

    response = client.post(
        "/write", json={"operation": "add_workout", "payload": dict(PAYLOAD)}
    )

    assert response.status_code == 200
    local_time, zone_name = backend.calls[0][0], backend.calls[0][1]
    assert zone_name == "Europe/Berlin"
    assert local_time == f"{DAY}T09:15:00.000"  # Europe/Berlin ist im Oktober UTC+2.
    reconstructed = datetime.fromisoformat(local_time).replace(tzinfo=ZoneInfo(zone_name))
    assert reconstructed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") == (
        f"{DAY}T07:15:00Z"
    )


def test_local_start_time_falls_back_to_utc_for_unknown_zone():
    # Ein Fehlwert in GARMIN_TIMEZONE darf den Schreibpfad nicht sprengen:
    # Fallback auf UTC (localtime+Schluessel konsistent) statt Exception.
    start = gs.parse_start_time(f"{DAY}T07:15:00Z")

    local_time, zone_name = gs.local_start_time(start, "Mars/Olympus")

    assert zone_name == "UTC"
    assert local_time == f"{DAY}T07:15:00.000"


def test_write_with_default_timezone_keeps_utc_wall_time(db_path, monkeypatch):
    # Abnahmefall 3: Default `UTC` bleibt unveraendert (Wandzeit == UTC-Zeitpunkt).
    backend = RecordingGarmin()
    client = TestClient(
        ws.create_write_app(_real_service(db_path, "UTC", backend, monkeypatch))
    )

    response = client.post(
        "/write", json={"operation": "add_workout", "payload": dict(PAYLOAD)}
    )

    assert response.status_code == 200
    assert backend.calls[0][0] == f"{DAY}T07:15:00.000"
    assert backend.calls[0][1] == "UTC"


# --- Nachtrag MARAAA-43: strukturierte DB-Fehlerform -------------------------
#
# Abnahmefall 2: ein DB-Oeffnungsfehler in `WriteService.add_workout` liefert die
# strukturierte Form `{ok:false,error:{code,message,retryable}}`, nicht 500.


def test_write_reports_db_open_error_as_structured_error(tmp_path):
    # `db_path` ist ein Verzeichnis -> sqlite3.OperationalError beim Oeffnen.
    blocked = tmp_path / "als-verzeichnis"
    blocked.mkdir()
    client = _http_client(str(blocked), lambda _: FakeWriterClient())

    response = client.post(
        "/write", json={"operation": "add_workout", "payload": dict(PAYLOAD)}
    )

    assert response.status_code == 502
    body = response.json()
    assert body["ok"] is False
    assert set(body["error"]) == {"code", "message", "retryable"}
    assert body["error"]["code"] == "GARMIN_UNAVAILABLE"
    assert body["error"]["retryable"] is True


def test_write_reports_db_path_blocked_by_file_as_structured_error(tmp_path):
    # `db_path` liegt unter einer regulaeren Datei -> `connect_db` wirft OSError.
    blocker = tmp_path / "blocker"
    blocker.write_text("kein Verzeichnis", encoding="utf-8")
    client = _http_client(str(blocker / "dashboard.db"), lambda _: FakeWriterClient())

    response = client.post(
        "/write", json={"operation": "add_workout", "payload": dict(PAYLOAD)}
    )

    assert response.status_code == 502
    body = response.json()
    assert body["ok"] is False
    assert set(body["error"]) == {"code", "message", "retryable"}
    assert body["error"]["code"] == "GARMIN_UNAVAILABLE"
    assert body["error"]["retryable"] is True


def test_write_path_is_verifiable_with_mock_client_without_account(db_path):
    # Abnahmefall 3: GARMIN_SYNC_MOCK=1 ersetzt den echten Client durch den Mock.
    client = _http_client(db_path, lambda _: gs.MockGarminClient())
    response = client.post(
        "/write", json={"operation": "add_workout", "payload": dict(PAYLOAD)}
    )
    assert response.status_code == 200
    workout = response.json()["workout"]
    assert workout["activity_id"] is not None
    assert workout["activity_type"] == "running"
    assert set(workout) == WORKOUT_KEYS

    rows = {date: payload for kind, date, payload in _rows(db_path) if kind == "workout"}
    assert DAY in rows
    assert any(item["activity_id"] == workout["activity_id"] for item in rows[DAY])


def test_mock_flag_selects_mock_client():
    # GARMIN_SYNC_MOCK=1 -> MockGarminClient, sonst der echte Client.
    assert isinstance(gs.client_factory(True)(_config(":memory:")), gs.MockGarminClient)
    assert gs.client_factory(False) is gs.RealGarminClient


def test_mock_client_add_workout_feeds_the_read_path():
    # Der Mock ist zustandsbehaftet: die angelegte Aktivitaet erscheint im Read-Pfad.
    client = gs.MockGarminClient()
    payload = gs.validate_workout_payload(dict(PAYLOAD))
    raw = client.add_workout(payload)
    grouped = gs.normalize_workouts(client.get_activities(0, gs.WORKOUT_LIMIT))
    assert str(raw["activityId"]) == "9000000001"
    assert any(
        item["activity_id"] == "9000000001" for item in grouped.get(DAY, [])
    )


# --- Echter Transport: der Server bindet und antwortet -----------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_start_write_server_serves_over_real_transport(db_path):
    port = _free_port()
    ws.start_write_server(
        _config(db_path), lambda _: gs.MockGarminClient(), host="127.0.0.1", port=port
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10.0
    response: httpx.Response | None = None
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.post(
                f"{base}/write",
                json={"operation": "add_workout", "payload": dict(PAYLOAD)},
                timeout=2.0,
            )
            break
        except httpx.HTTPError as exc:  # Server noch nicht bereit
            last_error = exc
            time.sleep(0.1)
    assert response is not None, f"Write-Server nicht erreichbar: {last_error}"
    assert response.status_code == 200
    assert response.json()["workout"]["activity_id"] is not None
    assert any(kind == "workout" for kind, _, _ in _rows(db_path))
