def test_get_unknown_key_is_not_found(client):
    assert client.get("/api/state/nope").status_code == 404


def test_get_unset_allowed_key_is_not_found(client):
    assert client.get("/api/state/brightness").status_code == 404


def test_put_and_get_roundtrip(client):
    put = client.put("/api/state/active_tab", json={"value": "todos"})
    assert put.status_code == 200
    assert put.json() == {"key": "active_tab", "value": "todos"}

    get = client.get("/api/state/active_tab")
    assert get.status_code == 200
    assert get.json() == {"key": "active_tab", "value": "todos"}

    # Bootstrap spiegelt den aktiven Tab.
    assert client.get("/api/config").json()["active_tab"] == "todos"


def test_put_unknown_key_is_rejected(client):
    response = client.put("/api/state/nope", json={"value": "x"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_missing_value_is_rejected(client):
    assert client.put("/api/state/screen", json={}).status_code == 400


def test_put_screen_publishes_event(client):
    from app.routers.events import bus

    queue = bus.subscribe()
    try:
        client.put("/api/state/screen", json={"value": "off"})
        assert queue.get_nowait() == ("screen.changed", {"screen": "off"})
    finally:
        bus.unsubscribe(queue)


# --- Fokus-State (spec-focus C/D.1, WP-F1-1) --------------------------------


def seed_widget(conn, widget_id="w1"):
    conn.execute(
        "INSERT INTO widgets (id, name, version, manifest_json) VALUES (?, ?, ?, ?)",
        (widget_id, "Widget", "1.0.0", "{}"),
    )


def place(client, widget_id="w1", tab_id="dashboard", x=0, y=0, w=2, h=2):
    response = client.post(
        "/api/layout",
        json={
            "widget_id": widget_id,
            "tab_id": tab_id,
            "x": x,
            "y": y,
            "w": w,
            "h": h,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_get_focus_on_fresh_db_is_null(client):
    response = client.get("/api/state/focus")
    assert response.status_code == 200
    assert response.json() == {"key": "focus", "value": None}


def test_put_focus_resolves_instance_and_sets_active_tab(client, conn):
    seed_widget(conn)
    instance = place(client, tab_id="todos")

    response = client.put("/api/state/focus", json={"widget_id": "w1", "mode": "enlarge"})

    assert response.status_code == 200
    focus = response.json()["focus"]
    assert focus["widget_id"] == "w1"
    assert focus["instance_id"] == instance["id"]
    assert focus["tab_id"] == "todos"
    assert focus["mode"] == "enlarge"
    assert focus["ts"].endswith("Z")

    # Persistiert: GET liest dasselbe Objekt.
    assert client.get("/api/state/focus").json() == {"key": "focus", "value": focus}
    # PUT setzt active_tab auf den Ziel-Tab (criterion 5).
    assert client.get("/api/config").json()["active_tab"] == "todos"


def test_put_focus_unknown_widget_is_not_found(client):
    response = client.put("/api/state/focus", json={"widget_id": "weg", "mode": "highlight"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_put_focus_unknown_instance_is_validation_error(client, conn):
    seed_widget(conn)
    place(client)
    response = client.put(
        "/api/state/focus",
        json={"widget_id": "w1", "mode": "highlight", "instance_id": "fehlt"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_focus_foreign_instance_is_validation_error(client, conn):
    seed_widget(conn, "w1")
    seed_widget(conn, "w2")
    other = place(client, widget_id="w2")
    response = client.put(
        "/api/state/focus",
        json={"widget_id": "w1", "mode": "highlight", "instance_id": other["id"]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_focus_explicit_instance_overrides_default(client, conn):
    seed_widget(conn)
    place(client, x=0, y=0)
    target = place(client, x=4, y=0)

    focus = client.put(
        "/api/state/focus",
        json={"widget_id": "w1", "mode": "fullscreen", "instance_id": target["id"]},
    ).json()["focus"]

    assert focus["instance_id"] == target["id"]


def test_put_focus_picks_first_instance_by_tab_position_then_y_then_x(client, conn):
    seed_widget(conn)
    # Spaeterer Tab (position 2) und groesseres y zuerst angelegt.
    place(client, tab_id="widgets", x=0, y=0)
    place(client, tab_id="dashboard", x=4, y=4)
    expected = place(client, tab_id="dashboard", x=0, y=0)

    focus = client.put(
        "/api/state/focus", json={"widget_id": "w1", "mode": "highlight"}
    ).json()["focus"]

    # tab.position 0 (dashboard) vor 2 (widgets); innerhalb dessen y=0 vor y=4.
    assert focus["tab_id"] == "dashboard"
    assert focus["instance_id"] == expected["id"]


def test_put_focus_rejects_unknown_mode(client, conn):
    seed_widget(conn)
    place(client)
    response = client.put("/api/state/focus", json={"widget_id": "w1", "mode": "zoom"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_focus_rejects_extra_field(client, conn):
    seed_widget(conn)
    place(client)
    response = client.put(
        "/api/state/focus", json={"widget_id": "w1", "mode": "enlarge", "x": 1}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_delete_focus_returns_null_and_clears_state(client, conn):
    seed_widget(conn)
    place(client)
    client.put("/api/state/focus", json={"widget_id": "w1", "mode": "enlarge"})

    response = client.delete("/api/state/focus")

    assert response.status_code == 200
    assert response.json() == {"focus": None}
    assert client.get("/api/state/focus").json() == {"key": "focus", "value": None}


def test_config_exposes_focus_auto_reset_seconds_default(client):
    assert client.get("/api/config").json()["focus_auto_reset_seconds"] == 300


def test_config_focus_auto_reset_seconds_from_env(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("FOCUS_AUTO_RESET_SECONDS", "0")
    get_settings.cache_clear()
    assert client.get("/api/config").json()["focus_auto_reset_seconds"] == 0


def test_focus_auto_reset_seconds_is_non_negative():
    import pytest
    from pydantic import ValidationError

    from app.config import Settings

    assert Settings(focus_auto_reset_seconds=0).focus_auto_reset_seconds == 0
    with pytest.raises(ValidationError):
        Settings(focus_auto_reset_seconds=-1)
