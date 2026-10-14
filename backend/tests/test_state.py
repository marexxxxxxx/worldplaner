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
