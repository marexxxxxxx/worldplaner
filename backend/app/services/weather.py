"""Wetter ueber Open-Meteo (Spezifikation D.6), mit api_cache-Pufferung (TTL 600 s).

Kein API-Key. Bei Provider-Fehler wird der letzte Cache-Wert geliefert, sonst
ein Fehlerobjekt — nie ein 5xx-Absturz.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..config import get_settings

WEATHER_API_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT_SECONDS = 10

# WMO-Weather-Codes -> lesbare Condition.
WMO_CONDITIONS = {
    0: "clear",
    1: "mainly_clear",
    2: "partly_cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing_rime_fog",
    51: "light_drizzle",
    53: "drizzle",
    55: "dense_drizzle",
    56: "light_freezing_drizzle",
    57: "dense_freezing_drizzle",
    61: "slight_rain",
    63: "rain",
    65: "heavy_rain",
    66: "light_freezing_rain",
    67: "heavy_freezing_rain",
    71: "slight_snow",
    73: "snow",
    75: "heavy_snow",
    77: "snow_grains",
    80: "slight_rain_showers",
    81: "rain_showers",
    82: "violent_rain_showers",
    85: "slight_snow_showers",
    86: "heavy_snow_showers",
    95: "thunderstorm",
    96: "thunderstorm_slight_hail",
    99: "thunderstorm_heavy_hail",
}

UNAVAILABLE = {"error_code": "WEATHER_UNAVAILABLE"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def condition(code: int | None) -> str:
    if code is None:
        return "unknown"
    return WMO_CONDITIONS.get(int(code), "unknown")


# --- Provider (in Tests ersetzbar) -----------------------------------------

def fetch_current(latitude: float, longitude: float, timezone_name: str) -> dict[str, Any]:
    response = httpx.get(
        WEATHER_API_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
            "timezone": timezone_name,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    current = response.json()["current"]
    return {
        "temperature_c": current["temperature_2m"],
        "condition": condition(current["weather_code"]),
        "humidity": current["relative_humidity_2m"],
        "wind_kmh": current["wind_speed_10m"],
        "fetched_at": _iso(_now()),
    }


def fetch_forecast(
    latitude: float, longitude: float, timezone_name: str, days: int
) -> list[dict[str, Any]]:
    response = httpx.get(
        WEATHER_API_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            "forecast_days": days,
            "timezone": timezone_name,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    daily = response.json()["daily"]
    return [
        {
            "date": daily["time"][index],
            "condition": condition(daily["weather_code"][index]),
            "temperature_max_c": daily["temperature_2m_max"][index],
            "temperature_min_c": daily["temperature_2m_min"][index],
        }
        for index in range(len(daily["time"]))
    ]


# --- api_cache --------------------------------------------------------------

def _read_cache(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT payload_json FROM api_cache WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["payload_json"])


def _read_fresh(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT payload_json, expires_at FROM api_cache WHERE key = ?", (key,)
    ).fetchone()
    if row is None or row["expires_at"] <= _iso(_now()):
        return None
    return json.loads(row["payload_json"])


def _store(conn: sqlite3.Connection, key: str, payload: Any, ttl_seconds: int) -> None:
    now = _now()
    conn.execute(
        "INSERT INTO api_cache (key, payload_json, fetched_at, expires_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET payload_json = excluded.payload_json, "
        "fetched_at = excluded.fetched_at, expires_at = excluded.expires_at",
        (key, json.dumps(payload), _iso(now), _iso(now + timedelta(seconds=ttl_seconds))),
    )


def _cache_key(kind: str, extra: str = "") -> str:
    settings = get_settings()
    base = f"weather:{kind}:{settings.weather_latitude},{settings.weather_longitude}"
    return f"{base}:{extra}" if extra else base


# --- oeffentliche API -------------------------------------------------------

def current(conn: sqlite3.Connection) -> dict[str, Any]:
    settings = get_settings()
    key = _cache_key("current")
    cached = _read_fresh(conn, key)
    if cached is not None:
        return cached
    try:
        payload = fetch_current(
            settings.weather_latitude, settings.weather_longitude, settings.weather_timezone
        )
    except Exception:
        last = _read_cache(conn, key)
        return last if last is not None else dict(UNAVAILABLE)
    _store(conn, key, payload, settings.weather_cache_ttl_seconds)
    return payload


def forecast(conn: sqlite3.Connection, days: int) -> dict[str, Any]:
    settings = get_settings()
    key = _cache_key("forecast", str(days))
    cached = _read_fresh(conn, key)
    if cached is not None:
        return {"daily": cached}
    try:
        daily = fetch_forecast(
            settings.weather_latitude, settings.weather_longitude, settings.weather_timezone, days
        )
    except Exception:
        last = _read_cache(conn, key)
        if last is not None:
            return {"daily": last}
        return {**UNAVAILABLE, "daily": []}
    _store(conn, key, daily, settings.weather_cache_ttl_seconds)
    return {"daily": daily}
