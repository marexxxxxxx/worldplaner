"""Mealie-Wochenplan (spec-phase2 D/WP-P2-3), Pull + api_cache-Pufferung.

Pull-Adapter nach dem Wetter-Muster (D12): das Backend ruft die Mealie-REST-API
(`GET {MEALIE_URL}/api/groups/mealplans/this-week`, Mealie v2) mit Bearer-Token,
normalisiert jede Mahlzeit auf {date, meal_type, recipe_id, recipe_name,
servings} und puffert die Antwort in `api_cache` (Schluessel
`mealie:mealplan:<YYYY-MM-DD>`). Bei Provider-Fehler wird der letzte Cache-Wert
geliefert, sonst ein Fehlerobjekt — nie ein 5xx-Absturz.

Ein leeres Ergebnis ist kein Fehler.
"""

import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from ..config import get_settings

SOURCE_PATH = "/api/groups/mealplans/this-week"
UNAVAILABLE: dict[str, Any] = {"meals": [], "error_code": "MEALIE_UNAVAILABLE"}

DEFAULT_CACHE_TTL_SECONDS = 600
DEFAULT_DAYS_AHEAD = 7
DEFAULT_TIMEOUT_SECONDS = 10

# Fenster-Grenzen: `days` ist der abzurufende Zeitraum ab heute (inklusive).
MIN_DAYS = 1
MAX_DAYS = 7

# Sortierung der Mahlzeiten innerhalb eines Tages (feste Reihenfolge der
# Mahlzeitentypen aus Mealie; unbekannte Typen landen am Ende).
_MEAL_TYPE_ORDER = {
    "breakfast": 0,
    "lunch": 1,
    "dinner": 2,
    "side": 3,
    "snack": 4,
    "dessert": 5,
    "drink": 6,
    "meal": 7,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return _now().strftime("%Y-%m-%d")


def cache_key(day: str | None = None) -> str:
    """Cache-Schluessel `mealie:mealplan:<YYYY-MM-DD>` (C.2)."""
    return f"mealie:mealplan:{day or _today()}"


def _config() -> dict[str, Any]:
    """Mealie-Einstellungen (spec-phase2 D/WP-P2-3).

    Die gemeinsame `config.py` wird gemaess Plan 10.2 additiv erst im
    Integrationsschritt erweitert; bis dahin liest dieser Adapter die
    dokumentierten Env-Variablen direkt, damit das Work-Package `config.py`
    nicht anfassen muss. Sobald der Integrationsschritt die Felder ergaenzt,
    greifen sie automatisch (Vorrang vor der Umgebung).
    """
    settings = get_settings()

    def text(attribute: str, env_name: str, default: str) -> str:
        value = getattr(settings, attribute, None)
        if value is None:
            value = os.environ.get(env_name, default)
        return str(value)

    def number(attribute: str, env_name: str, default: int) -> int:
        try:
            return int(text(attribute, env_name, str(default)))
        except ValueError:
            return default

    return {
        "url": text("mealie_url", "MEALIE_URL", ""),
        "api_token": text("mealie_api_token", "MEALIE_API_TOKEN", ""),
        "cache_ttl_seconds": number(
            "mealie_cache_ttl_seconds", "MEALIE_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS
        ),
        "days_ahead": number("mealie_days_ahead", "MEALIE_DAYS_AHEAD", DEFAULT_DAYS_AHEAD),
        "timeout_seconds": number(
            "mealie_timeout_seconds", "MEALIE_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS
        ),
    }


def days_ahead(config: dict[str, Any] | None = None) -> int:
    """Default-Zeitraum aus `MEALIE_DAYS_AHEAD`, geklemmt auf 1–7."""
    value = (config or _config())["days_ahead"]
    return max(MIN_DAYS, min(MAX_DAYS, value))


# --- Normalisierung ---------------------------------------------------------

def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _servings(value: Any) -> int:
    """Portionen als Integer; fehlend/ungueltig -> 1 (spec-phase2 D/WP-P2-3)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1


def normalize_meal(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Eine Mealie-Mealplan-Zeile -> Normalform (spec-phase2 D/WP-P2-3).

    `date` ist Pflicht; Eintraege ohne Datum werden verworfen (die Kachel
    gruppiert nach Tag). Die uebrigen Felder folgen der dokumentierten Herkunft
    mit ihren Fallbacks, damit das Schema stabil bleibt.
    """
    if not isinstance(entry, dict):
        return None
    day = _string(entry.get("date"))
    if day == "":
        return None

    recipe = entry.get("recipe") if isinstance(entry.get("recipe"), dict) else {}
    recipe_id = _string(recipe.get("id")) or _string(entry.get("recipe_id"))
    recipe_name = (
        _string(recipe.get("name"))
        or _string(recipe.get("title"))
        or _string(entry.get("title"))
    )
    meal_type = (
        _string(entry.get("entry_type")) or _string(entry.get("meal_type")) or "meal"
    )
    return {
        "date": day,
        "meal_type": meal_type,
        "recipe_id": recipe_id,
        "recipe_name": recipe_name,
        "servings": _servings(entry.get("servings")),
    }


def normalize_mealplan(raw: Any) -> list[dict[str, Any]]:
    """Mealie-Antwort -> Liste der Normalform.

    Akzeptiert sowohl eine blanke Liste (Mealie v2 `this-week`) als auch ein
    Objekt mit `items`-Liste.
    """
    if isinstance(raw, dict):
        entries = raw.get("items")
    else:
        entries = raw
    if not isinstance(entries, list):
        return []
    meals = (normalize_meal(entry) for entry in entries if isinstance(entry, dict))
    return [meal for meal in meals if meal is not None]


# --- Provider (in Tests ersetzbar) -----------------------------------------

def fetch_mealplan(base_url: str, api_token: str, timeout_seconds: int) -> dict[str, Any]:
    """Ruft `GET {MEALIE_URL}/api/groups/mealplans/this-week` und normalisiert (D/WP-P2-3)."""
    response = httpx.get(
        f"{base_url.rstrip('/')}{SOURCE_PATH}",
        headers={"Authorization": f"Bearer {api_token}"},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    return {"meals": normalize_mealplan(response.json()), "fetched_at": _iso(_now())}


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


# --- Zeitfenster ------------------------------------------------------------

def _window(today: str, days: int) -> tuple[str, str]:
    """Zeitscheibe `days` ab heute, inklusive: [heute, heute+days-1] (D/WP-P2-3)."""
    try:
        start = date.fromisoformat(today)
    except ValueError:
        start = _now().date()
    end = start + timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def _in_window(day: Any, start: str, end: str) -> bool:
    """ISO-Datum ('YYYY-MM-DD') lexikografisch im Fenster."""
    return isinstance(day, str) and start <= day <= end


def _sort_key(meal: dict[str, Any]) -> tuple[str, int, str]:
    return (
        meal["date"],
        _MEAL_TYPE_ORDER.get(meal["meal_type"], len(_MEAL_TYPE_ORDER)),
        meal["recipe_name"],
    )


def _filter(payload: dict[str, Any], days: int) -> dict[str, Any]:
    meals = payload.get("meals") if isinstance(payload, dict) else None
    meals = meals if isinstance(meals, list) else []
    start, end = _window(_today(), days)
    selected = [meal for meal in meals if _in_window(meal.get("date"), start, end)]
    result: dict[str, Any] = {"meals": sorted(selected, key=_sort_key)}
    fetched_at = payload.get("fetched_at") if isinstance(payload, dict) else None
    if isinstance(fetched_at, str):
        result["fetched_at"] = fetched_at
    return result


# --- oeffentliche API -------------------------------------------------------

def mealplan(conn: sqlite3.Connection, days: int) -> dict[str, Any]:
    """Wochenplan: frischer Cache, sonst Provider; bei Fehler letzter Cache.

    Ergebnis ist auf `days` Tage ab heute gefiltert; ohne Cache und ohne
    erreichbaren Provider kommt das Fehlerobjekt `MEALIE_UNAVAILABLE`.
    """
    config = _config()
    window = max(MIN_DAYS, min(MAX_DAYS, days))
    key = cache_key()
    cached = _read_fresh(conn, key)
    if cached is not None:
        return _filter(cached, window)
    try:
        payload = fetch_mealplan(
            config["url"], config["api_token"], config["timeout_seconds"]
        )
    except Exception:
        last = _read_cache(conn, key)
        if last is not None:
            return _filter(last, window)
        return dict(UNAVAILABLE)
    _store(conn, key, payload, config["cache_ttl_seconds"])
    return _filter(payload, window)
