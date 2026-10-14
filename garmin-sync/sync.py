"""Garmin-Sync — einziger Schreiber von `garmin_cache` (Spezifikation G, WP-5/B2).

Ruft periodisch Gesundheitsdaten (sleep, readiness, workout, vitals) vom
Garmin-Account ab, normalisiert sie auf das verbindliche Payload-Schema G.5 und
schreibt sie nach `garmin_cache`. Das Backend liest diese Tabelle ausschliesslich
(A.2/D4, Single-Writer-Regel).

Konfiguration exakt nach G.2, Token-Volume nach G.3, Intervall/Backoff nach G.4,
Payload-Schema nach G.5. Der echte Garmin-Login inkl. MFA ist Auftraggeber-Setup
(L.3.3); ohne gueltige Credentials laeuft der Dienst sauber weiter, ohne Crash.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("garmin-sync")

# Defaults aus G.2.
DEFAULT_SYNC_INTERVAL_SECONDS = 1200
DEFAULT_BACKOFF_BASE_SECONDS = 60
DEFAULT_BACKOFF_MAX_ATTEMPTS = 5
DEFAULT_BACKOFF_CAP_SECONDS = 3600
DEFAULT_TOKEN_DIR = "/data/token"
# H.1: der Sync schreibt in dieselbe DB-Datei (Volume sqlite-data) wie das Backend.
DEFAULT_DB_PATH = "/data/db/dashboard.db"
# Lokale Zeitzone fuer das manuelle Anlegen einer Aktivitaet (WP-P2-5). Garmin
# erwartet die Startzeit als lokale Zeit plus Zeitzonen-Schluessel.
DEFAULT_TIMEZONE = "UTC"

# G.5: get_activities(0, limit) — Anzahl der zuletzt abgerufenen Aktivitaeten.
WORKOUT_LIMIT = 50

# Token-Datei von python-garminconnect >= 0.3 (G.3).
TOKEN_FILE_NAME = "garmin_tokens.json"


class SyncError(Exception):
    """Basisklasse der Sync-Fehler; steuert das Backoff-Verhalten (G.4)."""


class AuthError(SyncError):
    """Auth-/Token-Fehler (401): einmal loggen, kein Backoff-Hammering (G.4.4)."""


class TransientError(SyncError):
    """Netz-/5xx-Fehler: exponentieller Backoff (G.4.2)."""


@dataclass(frozen=True)
class Config:
    """Laufzeitkonfiguration aus der Umgebung (G.2 / H.1)."""

    email: str
    password: str
    sync_interval_seconds: int
    backoff_base_seconds: int
    backoff_max_attempts: int
    backoff_cap_seconds: int
    token_dir: str
    db_path: str
    timezone: str = DEFAULT_TIMEZONE


def _int_env(env: dict[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def load_config(env: dict[str, str] | None = None) -> Config:
    """Liest die G.2-Variablen; `GARMIN_DB_PATH`/`GARMIN_TOKEN_DIR` nach H.1/G.3."""
    source = os.environ if env is None else env
    return Config(
        email=(source.get("GARMIN_EMAIL") or "").strip(),
        password=source.get("GARMIN_PASSWORD") or "",
        sync_interval_seconds=_int_env(
            source, "GARMIN_SYNC_INTERVAL_SECONDS", DEFAULT_SYNC_INTERVAL_SECONDS
        ),
        backoff_base_seconds=_int_env(
            source, "GARMIN_BACKOFF_BASE_SECONDS", DEFAULT_BACKOFF_BASE_SECONDS
        ),
        backoff_max_attempts=_int_env(
            source, "GARMIN_BACKOFF_MAX_ATTEMPTS", DEFAULT_BACKOFF_MAX_ATTEMPTS
        ),
        backoff_cap_seconds=_int_env(
            source, "GARMIN_BACKOFF_CAP_SECONDS", DEFAULT_BACKOFF_CAP_SECONDS
        ),
        token_dir=source.get("GARMIN_TOKEN_DIR") or DEFAULT_TOKEN_DIR,
        db_path=source.get("GARMIN_DB_PATH") or DEFAULT_DB_PATH,
        timezone=source.get("GARMIN_TIMEZONE") or DEFAULT_TIMEZONE,
    )


def backoff_seconds(
    attempt: int, base: int, cap: int, interval: int
) -> int:
    """G.4.2: `base * 2^attempt`, gedeckelt auf `min(cap, interval)`."""
    ceiling = min(cap, interval)
    return min(base * (2**attempt), ceiling)


def backoff_waits(config: Config) -> list[int]:
    """Wartezeiten fuer `attempt = 0..max_attempts-1` (G.4.2)."""
    return [
        backoff_seconds(
            attempt,
            config.backoff_base_seconds,
            config.backoff_cap_seconds,
            config.sync_interval_seconds,
        )
        for attempt in range(config.backoff_max_attempts)
    ]


# --- Zeit-/Werthilfen (Konvention: ISO-8601 UTC `...Z`, Datum `YYYY-MM-DD`) ---


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_date(moment: datetime | None = None) -> str:
    value = moment or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _from_ms(milliseconds: Any) -> datetime | None:
    """Garmin liefert Zeitstempel als Epoch-Millisekunden."""
    if milliseconds is None:
        return None
    try:
        return datetime.fromtimestamp(int(milliseconds) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _from_gmt_string(value: Any) -> datetime | None:
    """Parst Garmins `*GMT`-Strings (z. B. ``2026-10-13 07:15:00``) als UTC."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1]
    try:
        parsed = datetime.fromisoformat(text.replace(" ", "T"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _entries(raw: Any) -> list[dict[str, Any]]:
    """`get_body_battery` liefert eine Liste; defensiv auch ein Einzelobjekt."""
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    if isinstance(raw, dict):
        return [raw]
    return []


def _body_battery_points(entries: list[dict[str, Any]]) -> list[tuple[Any, int]]:
    """Alle Tageswerte aus `bodyBatteryValuesArray` (Liste ``[ts, value, ...]``)."""
    points: list[tuple[Any, int]] = []
    for entry in entries:
        for point in entry.get("bodyBatteryValuesArray") or []:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                value = _as_int(point[1])
                if value is not None:
                    points.append((point[0], value))
    points.sort(key=lambda point: point[0] if point[0] is not None else 0)
    return points


# --- Normalisierung auf das verbindliche Payload-Schema G.5 -------------------


def normalize_sleep(raw: dict[str, Any] | None) -> dict[str, Any]:
    """G.5 `kind=sleep`: genau die acht Felder der Tabelle."""
    dto = (raw or {}).get("dailySleepDTO") or {}
    score = (dto.get("sleepScores") or {}).get("overall") or {}
    return {
        "sleep_start": _iso_utc(_from_ms(dto.get("sleepStartTimestampGMT"))),
        "sleep_end": _iso_utc(_from_ms(dto.get("sleepEndTimestampGMT"))),
        "duration_s": _as_int(dto.get("sleepTimeSeconds")),
        "deep_sleep_s": _as_int(dto.get("deepSleepSeconds")),
        "light_sleep_s": _as_int(dto.get("lightSleepSeconds")),
        "rem_sleep_s": _as_int(dto.get("remSleepSeconds")),
        "awake_s": _as_int(dto.get("awakeSleepSeconds")),
        "sleep_score": _as_int(score.get("value")),
    }


def normalize_readiness(
    summary: dict[str, Any],
    body_battery: list[dict[str, Any]],
    stress: dict[str, Any],
    sleep_score: int | None,
) -> dict[str, Any]:
    """G.5 `kind=readiness`: Readiness-Ersatz nach D11."""
    values = [value for _, value in _body_battery_points(body_battery)]
    return {
        "body_battery": values[-1] if values else None,
        "body_battery_high": max(values) if values else None,
        "body_battery_low": min(values) if values else None,
        "stress_avg": _as_int(stress.get("avgStressLevel")),
        "sleep_score": sleep_score,
        "resting_hr": _as_int(summary.get("restingHeartRate")),
    }


def normalize_vitals(
    summary: dict[str, Any],
    body_battery: list[dict[str, Any]],
    stress: dict[str, Any],
) -> dict[str, Any]:
    """G.5 `kind=vitals`."""
    values = [value for _, value in _body_battery_points(body_battery)]
    return {
        "steps": _as_int(summary.get("totalSteps")),
        "resting_hr": _as_int(summary.get("restingHeartRate")),
        "stress_avg": _as_int(stress.get("avgStressLevel")),
        "body_battery": values[-1] if values else None,
        "floors_climbed": _as_int(summary.get("floorsAscended")),
        "calories_active": _as_int(summary.get("activeKilocalories")),
    }


def _workout_item(activity: dict[str, Any], date: str) -> dict[str, Any]:
    """Ein Eintrag der G.5-Workout-Liste."""
    return {
        "date": date,
        "activity_id": None
        if activity.get("activityId") is None
        else str(activity.get("activityId")),
        "activity_type": (activity.get("activityType") or {}).get("typeKey"),
        "activity_name": activity.get("activityName"),
        "start_time": _iso_utc(_from_gmt_string(activity.get("startTimeGMT"))),
        "duration_s": activity.get("duration"),
        "distance_m": activity.get("distance"),
        "calories": _as_int(activity.get("calories")),
        "avg_hr": _as_int(activity.get("averageHR")),
        "max_hr": _as_int(activity.get("maxHR")),
    }


def normalize_workouts(activities: Any) -> dict[str, list[dict[str, Any]]]:
    """G.5 `kind=workout`: Aktivitaeten nach Workout-Datum gruppiert."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for activity in activities or []:
        if not isinstance(activity, dict):
            continue
        start = _from_gmt_string(activity.get("startTimeGMT"))
        if start is None:
            continue
        date = start.strftime("%Y-%m-%d")
        grouped.setdefault(date, []).append(_workout_item(activity, date))
    return grouped


# --- Schreibpfad: Validierung und Normalisierung (WP-P2-5) -------------------


class WriteValidationError(ValueError):
    """Ungueltige Eingabe fuer den internen Schreibpfad (WP-P2-5)."""


# Erlaubte Felder des Schreib-Payloads (spec-phase2 D/WP-P2-5).
WORKOUT_FIELDS = (
    "activity_type",
    "start_time",
    "duration_s",
    "distance_m",
    "calories",
    "title",
)


def parse_start_time(value: Any) -> datetime | None:
    """Parst ISO-8601 (UTC ``...Z`` oder Offset) zu einem aware datetime in UTC."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_workout_payload(payload: Any) -> dict[str, Any]:
    """Prueft das Payload-Schema an der Grenze (Pflichtfelder, Wertebereiche).

    Liefert das normalisierte Payload; wirft ``WriteValidationError`` mit einer
    konkreten Meldung, wenn ein Feld fehlt oder falsch typisiert ist.
    """
    if not isinstance(payload, dict):
        raise WriteValidationError("payload muss ein Objekt sein")
    unknown = sorted(set(payload) - set(WORKOUT_FIELDS))
    if unknown:
        raise WriteValidationError("Unbekannte Felder: " + ", ".join(unknown))

    activity_type = payload.get("activity_type")
    if not isinstance(activity_type, str) or not activity_type.strip():
        raise WriteValidationError("activity_type ist erforderlich (nicht leer)")

    start = parse_start_time(payload.get("start_time"))
    if start is None:
        raise WriteValidationError("start_time ist erforderlich (ISO-8601 UTC)")

    duration = payload.get("duration_s")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration < 1:
        raise WriteValidationError("duration_s ist erforderlich (Ganzzahl >= 1)")

    normalized: dict[str, Any] = {
        "activity_type": activity_type.strip(),
        "start_time": _iso_utc(start),
        "duration_s": duration,
    }

    distance = payload.get("distance_m")
    if distance is not None:
        if (
            isinstance(distance, bool)
            or not isinstance(distance, (int, float))
            or distance < 0
        ):
            raise WriteValidationError("distance_m muss eine Zahl >= 0 sein")
        normalized["distance_m"] = float(distance)

    calories = payload.get("calories")
    if calories is not None:
        if isinstance(calories, bool) or not isinstance(calories, int) or calories < 0:
            raise WriteValidationError("calories muss eine Ganzzahl >= 0 sein")
        normalized["calories"] = calories

    title = payload.get("title")
    if title is not None:
        if not isinstance(title, str):
            raise WriteValidationError("title muss eine Zeichenkette sein")
        normalized["title"] = title

    return normalized


def workout_item_from_payload(
    payload: dict[str, Any], activity_id: Any
) -> dict[str, Any]:
    """Baut einen G.5-Workout-Eintrag aus dem Schreib-Payload (Fallback)."""
    start = parse_start_time(payload["start_time"])
    return {
        "date": start.strftime("%Y-%m-%d"),
        "activity_id": None if activity_id is None else str(activity_id),
        "activity_type": payload["activity_type"],
        "activity_name": payload.get("title"),
        "start_time": _iso_utc(start),
        "duration_s": payload["duration_s"],
        "distance_m": payload.get("distance_m"),
        "calories": payload.get("calories"),
        "avg_hr": None,
        "max_hr": None,
    }


# --- Datenbank (einziger Schreiber von `garmin_cache`, A.2/D4) ---------------


def connect_db(path: str) -> sqlite3.Connection:
    """Verbindung mit denselben PRAGMAs wie das Backend (C, A.4)."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def write_cache(
    conn: sqlite3.Connection,
    kind: str,
    date: str,
    payload: Any,
    fetched_at: str | None = None,
) -> None:
    """Upsert einer Cache-Zeile (UNIQUE(kind, date)) — `fetched_at` wird gesetzt."""
    conn.execute(
        "INSERT INTO garmin_cache (kind, date, payload_json, fetched_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(kind, date) DO UPDATE SET "
        "payload_json = excluded.payload_json, fetched_at = excluded.fetched_at",
        (
            kind,
            date,
            json.dumps(payload, ensure_ascii=False),
            fetched_at or utc_now(),
        ),
    )
    conn.commit()


# --- Client-Abstraktion (injizierbar; echter Client erst im Container) -------


def _is_auth_error(exc: BaseException) -> bool:
    """401/403 bzw. `GarminConnectAuthenticationError` → Auth-Fehler (G.4.4)."""
    for cls in type(exc).__mro__:
        if cls.__name__ == "GarminConnectAuthenticationError":
            return True
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    return isinstance(status, int) and status in (401, 403)


def translate_error(exc: BaseException) -> SyncError:
    """Uebersetzt Library-/Netz-Fehler in AuthError oder TransientError (G.4)."""
    if _is_auth_error(exc):
        return AuthError(str(exc))
    return TransientError(str(exc))


class RealGarminClient:
    """Duenne Huelle um `python-garminconnect` (Login + die fuenf G.5-Quellen)."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._garmin: Any = None

    def login(self) -> None:
        from garminconnect import Garmin

        # retry_attempts=0: das Backoff aus G.4 ist die einzige Wiederholungsschicht.
        garmin = Garmin(
            self._config.email or None,
            self._config.password or None,
            retry_attempts=0,
        )
        try:
            # G.3: Session im Token-Volume persistieren (Token-Login bevorzugt).
            garmin.login(self._config.token_dir)
        except Exception as exc:  # noqa: BLE001 - gezielt uebersetzt, nicht geschluckt
            raise translate_error(exc) from exc
        self._garmin = garmin

    def _call(self, method: Callable[..., Any], *args: Any) -> Any:
        if self._garmin is None:
            raise AuthError("Nicht angemeldet")
        try:
            return method(*args)
        except Exception as exc:  # noqa: BLE001 - gezielt uebersetzt, nicht geschluckt
            raise translate_error(exc) from exc

    def get_sleep_data(self, date: str) -> Any:
        return self._call(self._garmin.get_sleep_data, date)

    def get_stress_data(self, date: str) -> Any:
        return self._call(self._garmin.get_stress_data, date)

    def get_body_battery(self, date: str) -> Any:
        return self._call(self._garmin.get_body_battery, date)

    def get_user_summary(self, date: str) -> Any:
        return self._call(self._garmin.get_user_summary, date)

    def get_activities(self, start: int, limit: int) -> Any:
        return self._call(self._garmin.get_activities, start, limit)

    def add_workout(self, payload: dict[str, Any]) -> Any:
        """Legt eine abgeschlossene Aktivitaet an (WP-P2-5, D14/D16).

        Nutzt `create_manual_activity` aus python-garminconnect; die Startzeit
        wird als lokale Zeit plus Zeitzonen-Schluessel uebergeben.
        """
        start = parse_start_time(payload["start_time"])
        distance_m = payload.get("distance_m")
        distance_km = 0.0 if distance_m is None else float(distance_m) / 1000.0
        return self._call(
            self._garmin.create_manual_activity,
            start.strftime("%Y-%m-%dT%H:%M:%S.000"),
            self._config.timezone,
            payload["activity_type"],
            distance_km,
            payload["duration_s"] / 60.0,
            payload.get("title") or "",
        )


def _mock_ms(date: str, hour: int, minute: int) -> int:
    moment = datetime.fromisoformat(f"{date}T{hour:02d}:{minute:02d}:00").replace(
        tzinfo=timezone.utc
    )
    return int(moment.timestamp() * 1000)


class MockGarminClient:
    """Deterministischer Ersatz-Client fuer die Verifikation ohne echten Account.

    Nur aktiv bei `GARMIN_SYNC_MOCK=1` (Abnahme B2.2: mit gemocktem Garmin-Client
    schreibt `garmin-sync` eine `garmin_cache`-Zeile im Schema G.5). Liefert
    Beispieldaten im Rohformat von python-garminconnect.

    Die Aktivitaeten liegen im Speicher; `add_workout` haengt eine synthetische
    Aktivitaet an, damit der komplette Schreibpfad (WP-P2-5) ohne echten Account
    verifizierbar ist.
    """

    def __init__(self, activities: list[dict[str, Any]] | None = None) -> None:
        self._activities = list(activities) if activities is not None else [
            self._default_activity()
        ]
        self._next_activity_id = 9000000001

    @staticmethod
    def _default_activity() -> dict[str, Any]:
        return {
            "activityId": 1234567890,
            "activityType": {"typeKey": "running"},
            "activityName": "Morning Run",
            "startTimeGMT": f"{_utc_date()} 07:15:00",
            "duration": 2412.0,
            "distance": 5210.5,
            "calories": 412,
            "averageHR": 149,
            "maxHR": 171,
        }

    def login(self) -> None:
        log.info(
            "GARMIN_SYNC_MOCK=1 aktiv: synthetische Garmin-Daten, keine echte Anmeldung."
        )

    def get_sleep_data(self, date: str) -> dict[str, Any]:
        previous = (datetime.fromisoformat(date) - timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
        return {
            "dailySleepDTO": {
                "sleepStartTimestampGMT": _mock_ms(previous, 22, 31),
                "sleepEndTimestampGMT": _mock_ms(date, 6, 42),
                "sleepTimeSeconds": 29460,
                "deepSleepSeconds": 5280,
                "lightSleepSeconds": 15600,
                "remSleepSeconds": 6180,
                "awakeSleepSeconds": 2400,
                "sleepScores": {"overall": {"value": 84}},
            }
        }

    def get_stress_data(self, date: str) -> dict[str, Any]:
        return {"avgStressLevel": 34, "maxStressLevel": 88}

    def get_body_battery(self, date: str) -> list[dict[str, Any]]:
        return [
            {
                "date": date,
                "bodyBatteryValuesArray": [
                    [_mock_ms(date, 7, 0), 23],
                    [_mock_ms(date, 12, 0), 61],
                    [_mock_ms(date, 21, 0), 72],
                ],
            }
        ]

    def get_user_summary(self, date: str) -> dict[str, Any]:
        return {
            "totalSteps": 8432,
            "restingHeartRate": 58,
            "floorsAscended": 6.0,
            "activeKilocalories": 312,
        }

    def get_activities(self, start: int, limit: int) -> list[dict[str, Any]]:
        return list(self._activities[:limit])

    def add_workout(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Synthetische Aktivitaet anlegen (WP-P2-5, Verifikation ohne Account)."""
        start = parse_start_time(payload["start_time"])
        activity_id = self._next_activity_id
        self._next_activity_id += 1
        activity = {
            "activityId": activity_id,
            "activityType": {"typeKey": payload["activity_type"]},
            "activityName": payload.get("title"),
            "startTimeGMT": start.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": float(payload["duration_s"]),
            "distance": payload.get("distance_m"),
            "calories": payload.get("calories"),
            "averageHR": None,
            "maxHR": None,
        }
        self._activities.insert(0, activity)
        return activity


# --- Sync-Durchlauf ----------------------------------------------------------


def sync_once(
    conn: sqlite3.Connection, client: Any, now: datetime | None = None
) -> list[tuple[str, str]]:
    """Ein Durchlauf: fuenf Quellen abrufen, auf G.5 normalisieren, schreiben.

    Liefert die geschriebenen `(kind, date)`-Paare. Fehler werden nicht
    geschluckt: AuthError/TransientError kommen aus dem Client und werden
    weitergereicht; DB-Fehler werden als TransientError geworfen (G.4.2).
    """
    day = _utc_date(now)
    sleep_payload = normalize_sleep(client.get_sleep_data(day))
    stress = client.get_stress_data(day) or {}
    battery = _entries(client.get_body_battery(day))
    summary = client.get_user_summary(day) or {}
    activities = client.get_activities(0, WORKOUT_LIMIT)

    rows: list[tuple[str, str, Any]] = [
        ("sleep", day, sleep_payload),
        (
            "readiness",
            day,
            normalize_readiness(summary, battery, stress, sleep_payload["sleep_score"]),
        ),
        ("vitals", day, normalize_vitals(summary, battery, stress)),
    ]
    for workout_date, items in normalize_workouts(activities).items():
        rows.append(("workout", workout_date, items))

    try:
        for kind, date, payload in rows:
            write_cache(conn, kind, date, payload)
    except sqlite3.Error as exc:
        raise TransientError(f"Datenbank-Schreibfehler: {exc}") from exc
    return [(kind, date) for kind, date, _ in rows]


def sync_workouts_once(
    conn: sqlite3.Connection, client: Any
) -> dict[str, list[dict[str, Any]]]:
    """Nur die Workout-Quelle abrufen und nach G.5 schreiben (WP-P2-5 Re-Sync).

    Wird nach einem Schreibvorgang aufgerufen, damit `garmin_cache` die
    aktualisierte `workout`-Zeile enthaelt. DB-Fehler werden als
    ``TransientError`` gemeldet, Client-Fehler kommen aus dem Client.
    """
    activities = client.get_activities(0, WORKOUT_LIMIT)
    grouped = normalize_workouts(activities)
    try:
        for date, items in grouped.items():
            write_cache(conn, "workout", date, items)
    except sqlite3.Error as exc:
        raise TransientError(f"Datenbank-Schreibfehler: {exc}") from exc
    return grouped


# --- Scheduling (G.4) --------------------------------------------------------


def _has_credentials(config: Config) -> bool:
    return bool(config.email and config.password)


def _has_token(config: Config) -> bool:
    return os.path.exists(os.path.join(config.token_dir, TOKEN_FILE_NAME))


def client_factory(mock: bool) -> Callable[[Config], Any]:
    """Waehlt den Garmin-Client: Mock bei ``GARMIN_SYNC_MOCK=1``, sonst echt.

    Der Mock ersetzt die echte Anmeldung vollstaendig, damit der komplette
    Lese- und Schreibpfad ohne Garmin-Account verifizierbar ist (WP-P2-5).
    """
    if mock:
        return lambda _: MockGarminClient()
    return RealGarminClient


def run(
    config: Config,
    conn: sqlite3.Connection,
    client_factory: Callable[[Config], Any],
    *,
    require_credentials: bool = True,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    """Endlosschleife: planmaessiges Intervall und Exponential-Backoff (G.4).

    - Erfolg → naechster Sync nach `GARMIN_SYNC_INTERVAL_SECONDS` (G.4.1).
    - Netz-/5xx → `backoff_waits()`; danach Stop bis zum Intervall (G.4.2/G.4.3).
    - Auth/401 → genau ein Log, dann bis zum Intervall (G.4.4, kein Hammering).
    """
    if require_credentials and not (_has_credentials(config) or _has_token(config)):
        log.warning(
            "Keine Garmin-Credentials (GARMIN_EMAIL/GARMIN_PASSWORD) und keine "
            "Session unter %s. Der einmalige MFA-Login ist Auftraggeber-Setup "
            "(L.3.3); der Container laeuft ohne Sync weiter.",
            config.token_dir,
        )
        while True:
            sleeper(config.sync_interval_seconds)

    client: Any = None
    attempt = 0
    while True:
        try:
            if client is None:
                client = client_factory(config)
                client.login()
            sync_once(conn, client)
        except AuthError as exc:
            client = None
            log.error(
                "Garmin-Authentifizierung fehlgeschlagen: %s. Naechster Versuch "
                "erst in %ss (kein Backoff-Hammering, G.4.4).",
                exc,
                config.sync_interval_seconds,
            )
            attempt = 0
            sleeper(config.sync_interval_seconds)
            continue
        except TransientError as exc:
            client = None
            waits = backoff_waits(config)
            if attempt < len(waits):
                wait = waits[attempt]
                log.warning(
                    "Sync fehlgeschlagen (Versuch %d/%d): %s. Exponentieller "
                    "Backoff: warte %ss (G.4.2).",
                    attempt + 1,
                    len(waits),
                    exc,
                    wait,
                )
                attempt += 1
                sleeper(wait)
            else:
                log.error(
                    "Sync nach %d Wiederholungen erfolglos: %s. Cache bleibt "
                    "unveraendert; Stop bis zum naechsten Intervall (%ss) (G.4.3).",
                    len(waits),
                    exc,
                    config.sync_interval_seconds,
                )
                attempt = 0
                sleeper(config.sync_interval_seconds)
            continue
        attempt = 0
        sleeper(config.sync_interval_seconds)


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    config = load_config()
    mock = os.getenv("GARMIN_SYNC_MOCK") == "1"
    try:
        conn = connect_db(config.db_path)
    except (sqlite3.Error, OSError) as exc:
        log.error("Datenbank %s nicht nutzbar: %s", config.db_path, exc)
        return 1
    log.info(
        "garmin-sync startet (Intervall %ss, DB %s, Token %s).",
        config.sync_interval_seconds,
        config.db_path,
        config.token_dir,
    )
    factory: Callable[[Config], Any] = client_factory(mock)
    # Interner Write-Server im Hintergrundthread (WP-P2-5, D16): nur im
    # Compose-Netz erreichbar, kein Host-Port. Der Import erfolgt hier, weil
    # `write_server` seinerseits dieses Modul importiert.
    import write_server

    write_server.start_write_server(config, factory)
    run(config, conn, factory, require_credentials=not mock)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
