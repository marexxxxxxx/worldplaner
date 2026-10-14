import sqlite3


def test_health_ok(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


def test_health_degraded_when_db_unavailable(client, monkeypatch):
    from app import db

    def boom() -> bool:
        raise sqlite3.OperationalError("db weg")

    monkeypatch.setattr(db, "ping", boom)
    response = client.get("/api/health")
    assert response.status_code == 500
    assert response.json() == {"status": "degraded", "db": "unavailable"}


def test_config_bootstrap(client):
    response = client.get("/api/config")
    assert response.status_code == 200
    body = response.json()
    assert body["grid"] == {"columns": 12, "row_height_px": 96, "gap_px": 8}
    assert [tab["id"] for tab in body["tabs"]] == ["dashboard", "todos", "widgets"]
    assert body["active_tab"] == "dashboard"
