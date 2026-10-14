"""Baikal-Adapter `GET /api/baikal/events` (spec-phase2 D/WP-P2-1).

Die CalDAV-Quelle wird auf HTTP-Ebene gemockt (`app.services.baikal.httpx.request`),
so dass URL, Auth-Header, `REPORT`-Body, Normalisierung und `api_cache`-Pufferung
mitgeprueft werden. Der Router ist in main.py noch nicht registriert (das macht der
Integrationsschritt, Plan 10.2), darum wird hier eine lokale Test-App gebaut, die
dieselben Fehler-Handler wie die Produktion verwendet (`_register_error_handlers`),
damit `days`-Verletzungen als `400 VALIDATION_ERROR` ankommen.
"""

import base64
import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import get_settings
from app.main import _register_error_handlers
from app.routers import baikal
from app.services import baikal as baikal_service

CALDAV_URL = "https://baikal.example/dav.php/calendars/testuser/personal/"

# Realistische CalDAV-`multistatus`-Antwort (207, RFC 4791) mit zwei VEVENTs:
# ein Zeitraumtermin und ein Ganztagstermin (VALUE=DATE, ohne Ort/UID-Fallback).
CALDAV_XML = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/dav.php/calendars/testuser/personal/event1.ics</d:href>
    <d:propstat>
      <d:prop>
        <d:getetag>"1"</d:getetag>
        <c:calendar-data>BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:event1@baikal
SUMMARY:Zahnarzt
DTSTART:20261020T090000Z
DTEND:20261020T100000Z
LOCATION:Praxis
END:VEVENT
END:VCALENDAR</c:calendar-data>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav.php/calendars/testuser/personal/event2.ics</d:href>
    <d:propstat>
      <d:prop>
        <c:calendar-data>BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:event2@baikal
SUMMARY:Urlaub
DTSTART;VALUE=DATE:20261021
DTEND;VALUE=DATE:20261022
END:VEVENT
END:VCALENDAR</c:calendar-data>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>"""

EXPECTED_EVENTS = [
    {
        "uid": "event1@baikal",
        "summary": "Zahnarzt",
        "start": "2026-10-20T09:00:00Z",
        "end": "2026-10-20T10:00:00Z",
        "all_day": False,
        "location": "Praxis",
    },
    {
        "uid": "event2@baikal",
        "summary": "Urlaub",
        "start": "2026-10-21T00:00:00Z",
        "end": "2026-10-22T00:00:00Z",
        "all_day": True,
        "location": "",
    },
]


class FakeResponse:
    def __init__(self, text, status_code=207):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        raise NotImplementedError


@pytest.fixture
def baikal_env(monkeypatch):
    monkeypatch.setenv("BAIKAL_URL", CALDAV_URL)
    monkeypatch.setenv("BAIKAL_USERNAME", "testuser")
    monkeypatch.setenv("BAIKAL_PASSWORD", "secret")
    monkeypatch.setenv("BAIKAL_CACHE_TTL_SECONDS", "600")
    monkeypatch.setenv("BAIKAL_DAYS_AHEAD", "7")
    monkeypatch.setenv("BAIKAL_TIMEOUT_SECONDS", "10")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def baikal_client(db_path, baikal_env):
    db_module.init_db()
    app = FastAPI()
    _register_error_handlers(app)
    app.include_router(baikal.router, prefix="/api")
    with TestClient(app) as client:
        yield client


def _time_range(body: str) -> tuple[datetime, datetime]:
    match = re.search(r'start="(\d{8}T\d{6}Z)" end="(\d{8}T\d{6}Z)"', body)
    assert match, f"time-range fehlt im REPORT-Body: {body}"
    start, end = (datetime.strptime(value, "%Y%m%dT%H%M%SZ") for value in match.groups())
    return start, end


def test_events_normalizes_and_uses_cache(baikal_client, monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return FakeResponse(CALDAV_XML)

    monkeypatch.setattr(baikal_service.httpx, "request", fake_request)

    first = baikal_client.get("/api/baikal/events", params={"days": 7})
    assert first.status_code == 200
    body = first.json()
    assert body["events"] == EXPECTED_EVENTS
    assert isinstance(body["fetched_at"], str) and body["fetched_at"].endswith("Z")

    # Kriterium 1: Protokoll (REPORT), Quelle, Basic-Auth und Zeitfenster.
    assert calls[0]["method"] == "REPORT"
    assert calls[0]["url"] == CALDAV_URL
    expected_auth = "Basic " + base64.b64encode(b"testuser:secret").decode("ascii")
    assert calls[0]["headers"]["Authorization"] == expected_auth
    assert calls[0]["headers"]["Depth"] == "1"
    start, end = _time_range(calls[0]["content"].decode("utf-8"))
    assert end - start == timedelta(days=7)

    second = baikal_client.get("/api/baikal/events", params={"days": 7})
    assert second.json() == body
    assert len(calls) == 1  # zweiter Aufruf kommt aus dem Cache (TTL 600 s)


def test_days_defaults_to_env_days_ahead(baikal_client, monkeypatch):
    seen = {}

    def fake_request(method, url, **kwargs):
        seen["body"] = kwargs["content"].decode("utf-8")
        return FakeResponse(CALDAV_XML)

    monkeypatch.setattr(baikal_service.httpx, "request", fake_request)
    assert baikal_client.get("/api/baikal/events").status_code == 200
    start, end = _time_range(seen["body"])
    assert end - start == timedelta(days=7)  # BAIKAL_DAYS_AHEAD=7


def test_days_out_of_range_is_validation_error(baikal_client):
    for days in (0, 32):
        response = baikal_client.get("/api/baikal/events", params={"days": days})
        assert response.status_code == 400, f"days={days}"
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unreachable_source_without_cache(baikal_client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(baikal_service.httpx, "request", boom)
    response = baikal_client.get("/api/baikal/events", params={"days": 7})
    assert response.status_code == 200  # Kriterium 3: 200 statt 5xx
    assert response.json() == {"events": [], "error_code": "BAIKAL_UNAVAILABLE"}


def test_falls_back_to_expired_cache_on_provider_error(baikal_client, conn, monkeypatch):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"baikal:events:{today}"
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cached = {"events": EXPECTED_EVENTS, "fetched_at": past}
    conn.execute(
        "INSERT INTO api_cache (key, payload_json, fetched_at, expires_at) VALUES (?, ?, ?, ?)",
        (key, json.dumps(cached), past, past),
    )

    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(baikal_service.httpx, "request", boom)
    response = baikal_client.get("/api/baikal/events", params={"days": 7})
    assert response.status_code == 200
    assert response.json() == cached  # Kriterium 3: letzter Cache-Wert


def test_missing_credentials_is_unavailable(baikal_client, monkeypatch):
    monkeypatch.setenv("BAIKAL_URL", "")
    get_settings.cache_clear()

    def boom(*args, **kwargs):  # darf gar nicht erreicht werden
        raise AssertionError("Provider wurde ohne BAIKAL_URL aufgerufen")

    monkeypatch.setattr(baikal_service.httpx, "request", boom)
    response = baikal_client.get("/api/baikal/events")
    assert response.status_code == 200  # fehlende Credentials: kein Startfehler/5xx
    assert response.json() == {"events": [], "error_code": "BAIKAL_UNAVAILABLE"}


def test_missing_uid_falls_back_to_href(baikal_client, monkeypatch):
    xml = CALDAV_XML.replace("UID:event1@baikal\n", "")
    monkeypatch.setattr(
        baikal_service.httpx, "request", lambda *a, **k: FakeResponse(xml)
    )
    events = baikal_client.get("/api/baikal/events").json()["events"]
    assert events[0]["uid"].endswith("event1.ics")


def test_widget_baikal_calendar_is_complete():
    """Kriterium 2: Das Widget existiert vollstaendig und deklariert `baikal`."""
    from pathlib import Path

    widget_dir = Path(__file__).resolve().parents[2] / "widgets" / "baikal-calendar"
    manifest = json.loads((widget_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "baikal-calendar"
    assert manifest["permissions"] == ["baikal"]
    assert manifest["category"] in {"weather", "health", "productivity", "general"}
    assert all(
        isinstance(manifest["default_size"][axis], int) and manifest["default_size"][axis] >= 1
        for axis in ("w", "h")
    )
    for relative in (
        "frontend/widget.js",
        "frontend/style.css",
        "frontend/icon.svg",
        "README.md",
    ):
        assert (widget_dir / relative).is_file(), f"{relative} fehlt"
