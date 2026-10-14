"""Serverseitiges Widget-Permission-Gate (WP-P2-6, Spezifikation E.4).

Spricht das Backend direkt an (TestClient, kein Browser). Faelle 1-5 aus dem
Briefing: 403 ohne Permission, 200 mit Permission, erlaubt ohne Header,
404/500 bei unbekannter bzw. nicht lesbarer Widget-Id und die verbindliche
Spiegelung von `PERMISSION_RULES`.
"""

import json
import re
from pathlib import Path

import pytest

from app import permissions

JS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "frontend"
    / "js"
    / "widget-runtime.js"
)

EIGHT_PERMISSIONS = {
    "garmin_cache",
    "weather",
    "tasks",
    "state",
    "api_cache",
    "baikal",
    "grocy",
    "mealie",
}


def seed_widget(conn, widget_id, widget_permissions, manifest_json=None):
    if manifest_json is None:
        manifest_json = json.dumps(
            {"id": widget_id, "name": widget_id, "permissions": widget_permissions}
        )
    conn.execute(
        "INSERT INTO widgets (id, name, version, category, manifest_json, config_json) "
        "VALUES (?, ?, '1.0.0', 'general', ?, '{}')",
        (widget_id, widget_id, manifest_json),
    )


# --- Fälle 1-3 ---------------------------------------------------------------


def test_garmin_sleep_without_permission_is_forbidden(client, conn):
    seed_widget(conn, "w-no-garmin", [])
    response = client.get("/api/garmin/sleep", headers={"X-Widget-Id": "w-no-garmin"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_garmin_sleep_with_permission_is_allowed(client, conn):
    seed_widget(conn, "w-garmin", ["garmin_cache"])
    response = client.get("/api/garmin/sleep", headers={"X-Widget-Id": "w-garmin"})
    assert response.status_code == 200


def test_request_without_widget_header_is_allowed(client, conn):
    seed_widget(conn, "w-no-garmin", [])
    assert client.get("/api/garmin/sleep").status_code == 200


# --- Fall 4 ------------------------------------------------------------------


def test_unknown_widget_id_is_not_found(client):
    response = client.get("/api/garmin/sleep", headers={"X-Widget-Id": "ghost"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_unreadable_manifest_is_internal(client, conn):
    seed_widget(conn, "w-broken", [], manifest_json="{ not json")
    response = client.get("/api/garmin/sleep", headers={"X-Widget-Id": "w-broken"})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL"


def test_manifest_without_permissions_list_is_internal(client, conn):
    seed_widget(
        conn,
        "w-no-array",
        [],
        manifest_json=json.dumps({"id": "w-no-array", "permissions": "garmin_cache"}),
    )
    response = client.get("/api/garmin/sleep", headers={"X-Widget-Id": "w-no-array"})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL"


# --- Weitere gegatete Routen / Methoden-Scope --------------------------------


def test_other_permission_does_not_grant_garmin(client, conn):
    seed_widget(conn, "w-weather", ["weather"])
    assert (
        client.get("/api/garmin/sleep", headers={"X-Widget-Id": "w-weather"}).status_code
        == 403
    )


def test_weather_get_is_gated(client, conn):
    seed_widget(conn, "w-garmin", ["garmin_cache"])
    assert (
        client.get("/api/weather/current", headers={"X-Widget-Id": "w-garmin"}).status_code
        == 403
    )
    seed_widget(conn, "w-weather", ["weather"])
    assert (
        client.get("/api/weather/current", headers={"X-Widget-Id": "w-weather"}).status_code
        == 200
    )


def test_tasks_get_gated_but_post_is_not(client, conn):
    seed_widget(conn, "w-no-tasks", [])
    assert (
        client.get("/api/tasks", headers={"X-Widget-Id": "w-no-tasks"}).status_code == 403
    )
    created = client.post(
        "/api/tasks", json={"title": "x"}, headers={"X-Widget-Id": "w-no-tasks"}
    )
    assert created.status_code == 201


def test_state_get_gated_but_put_is_not(client, conn):
    seed_widget(conn, "w-no-state", [])
    assert (
        client.get("/api/state/active_tab", headers={"X-Widget-Id": "w-no-state"}).status_code
        == 403
    )
    written = client.put(
        "/api/state/active_tab",
        json={"value": "todos"},
        headers={"X-Widget-Id": "w-no-state"},
    )
    assert written.status_code == 200


def test_require_permission_rejects_unknown_permission():
    with pytest.raises(ValueError):
        permissions.require_permission("does-not-exist")


# --- Fall 5: Client-Spiegelung -------------------------------------------------


def test_permission_rules_are_identical_on_both_sides():
    assert set(permissions.PERMISSION_RULES) == EIGHT_PERMISSIONS
    source = JS_PATH.read_text(encoding="utf-8")
    block = source.split("const PERMISSION_RULES = {", 1)[1].split("};", 1)[0]
    client_permissions = set(re.findall(r"^\s*(\w+):", block, re.MULTILINE))
    assert client_permissions == EIGHT_PERMISSIONS


def test_widget_runtime_sends_widget_id_header_in_fetch_and_cache():
    source = JS_PATH.read_text(encoding="utf-8")
    assert source.count('"X-Widget-Id"') == 2
