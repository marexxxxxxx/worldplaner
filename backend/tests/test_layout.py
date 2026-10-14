def seed_widget(conn, widget_id="w1"):
    conn.execute(
        "INSERT INTO widgets (id, name, version, manifest_json) VALUES (?, ?, ?, ?)",
        (widget_id, "Widget", "1.0.0", "{}"),
    )


def create_layout(client, **overrides):
    payload = {"widget_id": "w1", "tab_id": "dashboard", "x": 0, "y": 0, "w": 2, "h": 2}
    payload.update(overrides)
    return client.post("/api/layout", json=payload)


def test_create_and_list(client, conn):
    seed_widget(conn)
    response = create_layout(client)
    assert response.status_code == 201
    body = response.json()
    assert body["widget_id"] == "w1"
    assert body["config_override"] == {}

    listing = client.get("/api/layout", params={"tab": "dashboard"}).json()
    assert len(listing) == 1
    assert {"id", "widget_id", "x", "y", "w", "h", "config_override"} <= set(listing[0])


def test_overlap_on_create_conflicts(client, conn):
    seed_widget(conn)
    create_layout(client, x=0, y=0, w=2, h=2)
    response = create_layout(client, x=1, y=1, w=2, h=2)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_bounds_violation_is_validation_error(client, conn):
    seed_widget(conn)
    response = create_layout(client, x=11, y=0, w=2, h=2)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_negative_coordinate_rejected(client, conn):
    seed_widget(conn)
    assert create_layout(client, x=-1).status_code == 400


def test_unknown_widget_is_not_found(client):
    response = create_layout(client, widget_id="nope")
    assert response.status_code == 404


def test_unknown_tab_is_not_found(client, conn):
    seed_widget(conn)
    response = create_layout(client, tab_id="nope")
    assert response.status_code == 404


def test_put_partial_update_and_overlap(client, conn):
    seed_widget(conn)
    first = create_layout(client, x=0, y=0, w=2, h=2).json()
    second = create_layout(client, x=4, y=0, w=2, h=2).json()

    conflict = client.put(f"/api/layout/{second['id']}", json={"x": 0, "y": 0})
    assert conflict.status_code == 409

    ok = client.put(f"/api/layout/{second['id']}", json={"x": 6})
    assert ok.status_code == 200
    assert ok.json()["x"] == 6
    assert ok.json()["y"] == 0
    assert first["id"] != second["id"]


def test_put_unknown_instance(client):
    assert client.put("/api/layout/fehlt", json={"x": 1}).status_code == 404


def test_delete_layout(client, conn):
    seed_widget(conn)
    instance = create_layout(client).json()
    assert client.delete(f"/api/layout/{instance['id']}").status_code == 204
    assert client.delete(f"/api/layout/{instance['id']}").status_code == 404
