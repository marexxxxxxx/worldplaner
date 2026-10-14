"""Grocy-Bestand (spec-phase2 D/WP-P2-2), Pull + api_cache-Pufferung.

Pull-Adapter nach dem Wetter-Muster (D12): das Backend ruft die Grocy-REST-API,
puffert die normalisierte Antwort in `api_cache` (Schluessel `grocy:stock`) und
das Widget pollt `GET /api/grocy/stock`. Bei Provider-Fehler wird der letzte
Cache-Wert geliefert, sonst ein Fehlerobjekt — nie ein 5xx-Absturz. Ein leeres
Ergebnis ist kein Fehler.
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..config import get_settings

CACHE_KEY = "grocy:stock"
UNAVAILABLE: dict[str, Any] = {"products": [], "error_code": "GROCY_UNAVAILABLE"}

DEFAULT_CACHE_TTL_SECONDS = 600
DEFAULT_TIMEOUT_SECONDS = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return _now().strftime("%Y-%m-%d")


def _config() -> dict[str, Any]:
    """Grocy-Einstellungen (spec-phase2 D/WP-P2-2).

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
        "url": text("grocy_url", "GROCY_URL", ""),
        "api_key": text("grocy_api_key", "GROCY_API_KEY", ""),
        "cache_ttl_seconds": number(
            "grocy_cache_ttl_seconds", "GROCY_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS
        ),
        "timeout_seconds": number(
            "grocy_timeout_seconds", "GROCY_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS
        ),
    }


# --- Normalisierung ---------------------------------------------------------

def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _unit_name(value: Any) -> str:
    if isinstance(value, dict):
        name = value.get("name")
        return name if isinstance(name, str) else ""
    return value if isinstance(value, str) else ""


def is_expired(best_before_date: str | None, today: str) -> bool:
    """MHD vor heute -> abgelaufen (ISO-Datum, lexikografisch vergleichbar)."""
    return bool(best_before_date) and best_before_date < today


def normalize_product(entry: dict[str, Any], today: str) -> dict[str, Any] | None:
    """Eine Grocy-Bestandszeile -> Normalform (spec-phase2 D/WP-P2-2).

    Grocy liefert je Produkt `product_id`, `amount`, `best_before_date` und ein
    verschachteltes `product`; die Einheit liegt je nach Version/Abfrage in
    `stock_unit_name`, `qu_name` oder `quantity_unit`. Fehlende optionale Felder
    werden auf leere Strings bzw. `0` gesetzt, damit das Schema stabil bleibt.
    Eintraege ohne `product_id`/`id` werden verworfen.
    """
    product_id = _to_int(entry.get("product_id", entry.get("id")))
    if product_id is None:
        return None

    product = entry.get("product")
    product = product if isinstance(product, dict) else {}
    name = entry.get("name") or product.get("name") or ""
    unit = (
        _unit_name(entry.get("stock_unit_name"))
        or _unit_name(entry.get("qu_name"))
        or _unit_name(entry.get("quantity_unit"))
        or _unit_name(product.get("qu_name"))
        or _unit_name(product.get("quantity_unit_stock"))
        or _unit_name(entry.get("unit"))
    )

    best_before_date = entry.get("best_before_date")
    if not isinstance(best_before_date, str) or best_before_date == "":
        best_before_date = None
    return {
        "product_id": product_id,
        "name": str(name),
        "amount": _to_float(entry.get("amount", entry.get("stock_amount"))),
        "unit": unit,
        "best_before_date": best_before_date,
        "is_expired": is_expired(best_before_date, today),
    }


def normalize_products(raw: Any, today: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    products = (
        normalize_product(entry, today) for entry in raw if isinstance(entry, dict)
    )
    return [product for product in products if product is not None]


# --- Provider (in Tests ersetzbar) -----------------------------------------

def fetch_stock(base_url: str, api_key: str, timeout_seconds: int) -> dict[str, Any]:
    """Ruft `GET {GROCY_URL}/api/stock` und liefert die Normalform (D/WP-P2-2)."""
    response = httpx.get(
        f"{base_url.rstrip('/')}/api/stock",
        headers={"GROCY-API-KEY": api_key},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    return {
        "products": normalize_products(response.json(), _today()),
        "fetched_at": _iso(_now()),
    }


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


# --- oeffentliche API -------------------------------------------------------

def stock(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gesamtbestand: frischer Cache, sonst Provider; bei Fehler letzter Cache."""
    config = _config()
    cached = _read_fresh(conn, CACHE_KEY)
    if cached is not None:
        return cached
    try:
        payload = fetch_stock(
            config["url"], config["api_key"], config["timeout_seconds"]
        )
    except Exception:
        last = _read_cache(conn, CACHE_KEY)
        return last if last is not None else dict(UNAVAILABLE)
    _store(conn, CACHE_KEY, payload, config["cache_ttl_seconds"])
    return payload
