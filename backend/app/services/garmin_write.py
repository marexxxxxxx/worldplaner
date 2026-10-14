"""Forward-Client zum internen Schreib-Endpunkt von `garmin-sync` (WP-P2-5, D16).

Der MCP-Server schreibt **nie** selbst nach Garmin: das Backend reicht den
Schreibwunsch an `garmin-sync` `POST /write` weiter (D3-Muster). `garmin-sync`
bleibt einziger Inhaber der Garmin-Session/Token (D16).

Fehler werden auf stabile Codes abgebildet (D17): `GARMIN_UNAVAILABLE` (502,
retryable), `GARMIN_AUTH_FAILED` (502, nicht retryable) und `VALIDATION_ERROR`
(400). Fehlermeldungen enthalten keine Geheimnisse, keine URLs mit
Zugangsdaten, keine Tokens.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..models import ApiError, garmin_auth_failed, garmin_unavailable, validation_error

# Internes Compose-Ziel (spec-phase2 D/WP-P2-5: Port 8002, kein Host-Port).
# Ueberschreibbar per Env; der Settings-Eintrag folgt in der Integration (WP-P2-INT).
DEFAULT_WRITE_URL = "http://garmin-sync:8002"
DEFAULT_TIMEOUT_SECONDS = 10.0


def _write_url() -> str:
    return (os.getenv("GARMIN_SYNC_WRITE_URL") or DEFAULT_WRITE_URL).rstrip("/")


def _timeout() -> float:
    raw = os.getenv("GARMIN_SYNC_WRITE_TIMEOUT_SECONDS")
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _unavailable(message: str) -> ApiError:
    return garmin_unavailable(message)


async def add_workout(
    payload: dict[str, Any], client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    """Legt eine Aktivitaet an und liefert das normalisierte Workout-Objekt.

    Erfolg -> das Workout-Dict aus `garmin-sync`. Sonst ``ApiError`` mit
    stabilem Code (siehe Modul-Docstring).
    """
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(base_url=_write_url(), timeout=_timeout())
    try:
        try:
            response = await client.post(
                "/write", json={"operation": "add_workout", "payload": payload}
            )
        except httpx.HTTPError:
            raise _unavailable("garmin-sync nicht erreichbar") from None
    finally:
        if own_client:
            await client.aclose()

    try:
        body = response.json()
    except ValueError:
        raise _unavailable("garmin-sync liefert keine gueltige Antwort") from None

    if (
        isinstance(body, dict)
        and body.get("ok") is True
        and isinstance(body.get("workout"), dict)
    ):
        return body["workout"]

    error = body.get("error") if isinstance(body, dict) else None
    error = error if isinstance(error, dict) else {}
    code = str(error.get("code") or "")
    message = str(error.get("message") or "garmin-sync lehnt den Schreibvorgang ab")
    if code == "GARMIN_AUTH_FAILED":
        raise garmin_auth_failed(message)
    if code == "VALIDATION_ERROR":
        raise validation_error(message)
    raise _unavailable(message)
