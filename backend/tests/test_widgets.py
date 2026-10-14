def seed_widget(conn, widget_id="w1", is_builtin=1, is_enabled=1):
    conn.execute(
        "INSERT INTO widgets "
        "(id, name, version, category, icon, is_builtin, is_enabled, max_instances, "
        " manifest_json, config_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            widget_id,
            "Schlaf",
            "1.0.0",
            "health",
            "icon.svg",
            is_builtin,
            is_enabled,
            1,
            '{"id": "w1", "name": "Schlaf"}',
            "{}",
        ),
    )


def test_list_empty(client):
    assert client.get("/api/widgets").json() == []


def test_list_and_get(client, conn):
    seed_widget(conn)
    listing = client.get("/api/widgets").json()
    assert len(listing) == 1
    assert listing[0]["is_enabled"] is True
    assert listing[0]["is_builtin"] is True

    detail = client.get("/api/widgets/w1").json()
    assert detail["manifest"] == {"id": "w1", "name": "Schlaf"}
    assert detail["config"] == {}
    assert detail["is_enabled"] is True


def test_get_unknown_widget(client):
    assert client.get("/api/widgets/nope").status_code == 404


def test_patch_disable_persists(client, conn):
    seed_widget(conn)
    response = client.patch("/api/widgets/w1", json={"is_enabled": False})
    assert response.status_code == 200
    assert response.json()["is_enabled"] is False
    assert client.get("/api/widgets").json()[0]["is_enabled"] is False


def test_patch_config_persists(client, conn):
    seed_widget(conn)
    response = client.patch("/api/widgets/w1", json={"config": {"units": "metric"}})
    assert response.status_code == 200
    assert client.get("/api/widgets/w1").json()["config"] == {"units": "metric"}


def test_patch_unknown_widget(client):
    assert client.patch("/api/widgets/nope", json={"is_enabled": True}).status_code == 404


def test_patch_empty_body_is_rejected(client, conn):
    seed_widget(conn)
    response = client.patch("/api/widgets/w1", json={})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_patch_unknown_field_is_rejected(client, conn):
    seed_widget(conn)
    assert client.patch("/api/widgets/w1", json={"name": "x"}).status_code == 400
