import json
from datetime import datetime, timezone

DAY = "2026-10-14"


def seed(conn, kind, date, payload, fetched_at=None):
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO garmin_cache (kind, date, payload_json, fetched_at) VALUES (?, ?, ?, ?)",
        (kind, date, json.dumps(payload), fetched_at),
    )


def test_sleep_empty_cache(client):
    response = client.get("/api/garmin/sleep", params={"date": DAY})
    assert response.status_code == 200
    assert response.json() == {"date": DAY, "data": None, "stale": True}


def test_sleep_returns_payload_unchanged(client, conn):
    payload = {"sleep_score": 84, "duration_s": 29460}
    seed(conn, "sleep", DAY, payload)
    response = client.get("/api/garmin/sleep", params={"date": DAY})
    assert response.json() == {"date": DAY, "data": payload, "stale": False}


def test_sleep_defaults_to_today(client, conn):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    seed(conn, "sleep", today, {"sleep_score": 1})
    response = client.get("/api/garmin/sleep")
    assert response.json()["date"] == today
    assert response.json()["data"] == {"sleep_score": 1}


def test_old_entry_is_stale(client, conn):
    seed(conn, "sleep", DAY, {"sleep_score": 1}, fetched_at="2020-01-01T00:00:00Z")
    response = client.get("/api/garmin/sleep", params={"date": DAY})
    assert response.json()["stale"] is True


def test_readiness_and_vitals_empty(client):
    for kind in ("readiness", "vitals"):
        response = client.get(f"/api/garmin/{kind}", params={"date": DAY})
        assert response.json() == {"date": DAY, "data": None, "stale": True}


def test_vitals_returns_payload(client, conn):
    payload = {"steps": 8432, "resting_hr": 58}
    seed(conn, "vitals", DAY, payload)
    body = client.get("/api/garmin/vitals", params={"date": DAY}).json()
    assert body["data"] == payload
    assert body["stale"] is False


def test_workouts_empty_cache(client):
    response = client.get("/api/garmin/workouts")
    assert response.json() == {"workouts": [], "stale": True}


def test_workouts_are_flattened_newest_first_and_limited(client, conn):
    seed(conn, "workout", "2026-10-12", [{"activity_id": "1"}, {"activity_id": "2"}])
    seed(conn, "workout", "2026-10-13", [{"activity_id": "3"}])
    response = client.get("/api/garmin/workouts", params={"limit": 2})
    assert [item["activity_id"] for item in response.json()["workouts"]] == ["3", "1"]
    assert response.json()["stale"] is False


def test_workouts_filter_by_date(client, conn):
    seed(conn, "workout", "2026-10-12", [{"activity_id": "1"}, {"activity_id": "2"}])
    seed(conn, "workout", "2026-10-13", [{"activity_id": "3"}])
    response = client.get("/api/garmin/workouts", params={"date": "2026-10-12"})
    assert [item["activity_id"] for item in response.json()["workouts"]] == ["1", "2"]


def test_invalid_date_is_rejected(client):
    assert client.get("/api/garmin/sleep", params={"date": "14.10.2026"}).status_code == 400
