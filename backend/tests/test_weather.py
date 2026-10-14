import json
from datetime import datetime, timedelta, timezone

CURRENT_KEY = "weather:current:48.1374,11.5755"


def test_current_uses_provider_then_cache(client, monkeypatch):
    from app.services import weather

    calls = {"count": 0}

    def fake_fetch(latitude, longitude, timezone_name):
        calls["count"] += 1
        return {
            "temperature_c": 12.0,
            "condition": "clear",
            "humidity": 70,
            "wind_kmh": 5.0,
            "fetched_at": "2026-10-14T10:00:00Z",
        }

    monkeypatch.setattr(weather, "fetch_current", fake_fetch)
    first = client.get("/api/weather/current")
    second = client.get("/api/weather/current")

    assert first.status_code == 200
    assert first.json()["condition"] == "clear"
    assert calls["count"] == 1  # zweiter Aufruf kommt aus dem Cache
    assert second.json() == first.json()


def test_current_without_provider_and_without_cache(client, monkeypatch):
    from app.services import weather

    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(weather, "fetch_current", boom)
    response = client.get("/api/weather/current")
    assert response.status_code == 200
    assert response.json() == {"error_code": "WEATHER_UNAVAILABLE"}


def test_current_falls_back_to_expired_cache_on_provider_error(client, conn, monkeypatch):
    from app.services import weather

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cached = {
        "temperature_c": 9.0,
        "condition": "rain",
        "humidity": 90,
        "wind_kmh": 3.0,
        "fetched_at": past,
    }
    conn.execute(
        "INSERT INTO api_cache (key, payload_json, fetched_at, expires_at) VALUES (?, ?, ?, ?)",
        (CURRENT_KEY, json.dumps(cached), past, past),
    )

    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(weather, "fetch_current", boom)
    assert client.get("/api/weather/current").json() == cached


def test_forecast_returns_daily(client, monkeypatch):
    from app.services import weather

    day = {"date": "2026-10-14", "condition": "clear", "temperature_max_c": 20, "temperature_min_c": 10}
    monkeypatch.setattr(weather, "fetch_forecast", lambda latitude, longitude, timezone_name, days: [day])
    body = client.get("/api/weather/forecast", params={"days": 1}).json()
    assert body == {"daily": [day]}


def test_forecast_days_out_of_range(client):
    assert client.get("/api/weather/forecast", params={"days": 0}).status_code == 400
    assert client.get("/api/weather/forecast", params={"days": 8}).status_code == 400


def test_forecast_without_provider_and_without_cache(client, monkeypatch):
    from app.services import weather

    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(weather, "fetch_forecast", boom)
    assert client.get("/api/weather/forecast").json() == {
        "error_code": "WEATHER_UNAVAILABLE",
        "daily": [],
    }
