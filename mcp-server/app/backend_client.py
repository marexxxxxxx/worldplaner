"""REST-Client zum Backend (Spezifikation D4/D).

Der MCP-Server haelt **keine** Datenbankverbindung und schreibt **nie** direkt
in SQLite; jeder Task-Zugriff geht ueber die REST-API (Entscheidung D3).
Fehlermeldungen enthalten keine Geheimnisse (keine URLs mit Zugangsdaten,
keine Tokens), sondern nur stabile Fehlercodes.
"""

from __future__ import annotations

from typing import Any

import httpx

# Stabile Fehlercodes der Backend-API (D.0). Alles andere wird zu INTERNAL.
_BACKEND_CODES = {
    "VALIDATION_ERROR",
    "NOT_FOUND",
    "CONFLICT",
    "FORBIDDEN",
    "INTERNAL",
    # D17 (spec-phase2): Codes des Garmin-Schreibpfads.
    "GARMIN_UNAVAILABLE",
    "GARMIN_AUTH_FAILED",
}

# 5xx-Codes, die durchgereicht (nicht zu BACKEND_UNAVAILABLE vereinfacht) werden.
_PROPAGATED_ERROR_CODES = {"GARMIN_UNAVAILABLE", "GARMIN_AUTH_FAILED"}


class BackendError(Exception):
    """Fehler aus dem Backend oder beim Transport dorthin.

    ``code`` folgt den MCP-Fehlercodes (F.2); ``partial`` traegt einen
    gemeldeten Teilstand (z. B. einen bereits angelegten Task).
    """

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        partial: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.partial = partial or {}


class BackendClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- Transport ---------------------------------------------------------

    async def _request(
        self, method: str, url: str, *, allow_404: bool = False, **kwargs: Any
    ) -> httpx.Response:
        try:
            response = await self._client.request(method, url, **kwargs)
        except httpx.HTTPError:
            raise BackendError(
                "BACKEND_UNAVAILABLE", "Backend nicht erreichbar", retryable=True
            ) from None
        if response.status_code == 404 and allow_404:
            return response
        if response.status_code >= 500:
            error = self._backend_error(response)
            if error.code in _PROPAGATED_ERROR_CODES:
                raise error
            raise BackendError(
                "BACKEND_UNAVAILABLE",
                f"Backend meldet Fehler (HTTP {response.status_code})",
                retryable=True,
            )
        if response.status_code >= 400:
            raise self._backend_error(response)
        return response

    @staticmethod
    def _backend_error(response: httpx.Response) -> BackendError:
        code = "INTERNAL"
        message = f"Backend lehnt Anfrage ab (HTTP {response.status_code})"
        retryable = False
        try:
            error = (response.json() or {}).get("error") or {}
            code = str(error.get("code") or code)
            message = str(error.get("message") or message)
            retryable = bool(error.get("retryable", False))
        except ValueError:
            pass
        if code not in _BACKEND_CODES:
            code = "INTERNAL"
        return BackendError(code, message, retryable)

    # --- Lesezugriffe ------------------------------------------------------

    async def health(self) -> str:
        """Liefert 'ok', 'degraded' oder 'unavailable' (D.1)."""
        try:
            response = await self._client.get("/api/health")
        except httpx.HTTPError:
            return "unavailable"
        if response.status_code == 200:
            try:
                return "ok" if response.json().get("status") == "ok" else "degraded"
            except ValueError:
                return "degraded"
        return "degraded"

    async def get_config(self) -> dict[str, Any]:
        return (await self._request("GET", "/api/config")).json()

    async def get_state(self, key: str) -> Any | None:
        """Dashboard-Zustand (D.7); ``None`` wenn der Key noch nicht gesetzt ist."""
        response = await self._request("GET", f"/api/state/{key}", allow_404=True)
        if response.status_code == 404:
            return None
        return (response.json() or {}).get("value")

    async def list_tasks(self, status: str, limit: int) -> list[dict[str, Any]]:
        response = await self._request(
            "GET", "/api/tasks", params={"status": status, "limit": limit}
        )
        return response.json()

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Einzelnen Task holen (die API bietet nur die Liste, D.4)."""
        for task in await self.list_tasks("all", 200):
            if task.get("id") == task_id:
                return task
        return None

    async def list_layout(self, tab: str | None = None) -> list[dict[str, Any]]:
        """Layout-Instanzen, optional auf einen Tab gefiltert (D.3).

        Liefert die Rohobjekte der Backend-API (Feld ``id`` = Instanz-UUID);
        die MCP-Schicht benennt ``id`` fuer den Aufrufer in ``instance_id`` um
        (spec-focus D.3). Eine Antwort, die keine Liste ist, ist ein
        Vertragsbruch und wird als BACKEND_UNAVAILABLE gemeldet — kein leeres
        Erfolgsergebnis.
        """
        params = {"tab": tab} if tab is not None else None
        response = await self._request("GET", "/api/layout", params=params)
        instances = response.json()
        if not isinstance(instances, list):
            raise BackendError(
                "BACKEND_UNAVAILABLE",
                "Backend liefert keine Layout-Liste",
                retryable=True,
            )
        return instances

    # --- Schreibzugriffe (ausschliesslich ueber REST, D3) ------------------

    async def set_state(self, key: str, value: str) -> None:
        await self._request("PUT", f"/api/state/{key}", json={"value": value})

    # --- Fokus (Ausbaustufe F1, spec-focus D.1/D.3) ------------------------

    async def set_focus(self, widget_id: str, mode: str) -> dict[str, Any]:
        """Setzt den Dashboard-Fokus (PUT /api/state/focus).

        Das Backend loest ``widget_id`` autoritativ zu ``instance_id``/``tab_id``
        auf und waehlt den Tab der Kachel; eine Kachel ohne Layout-Instanz
        liefert NOT_FOUND. Fehlt das ``focus``-Objekt in der Antwort, gilt die
        Antwort als ungueltig (kein stiller Erfolg).
        """
        response = await self._request(
            "PUT",
            "/api/state/focus",
            json={"widget_id": widget_id, "mode": mode},
        )
        focus = (response.json() or {}).get("focus")
        if not isinstance(focus, dict):
            raise BackendError(
                "BACKEND_UNAVAILABLE",
                "Backend liefert kein Fokus-Objekt",
                retryable=True,
            )
        return focus

    async def clear_focus(self) -> None:
        """Loescht den Dashboard-Fokus (DELETE /api/state/focus)."""
        await self._request("DELETE", "/api/state/focus")

    async def create_task(
        self,
        *,
        title: str,
        description: str,
        priority: str,
        due_at: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": title,
            "description": description,
            "priority": priority,
        }
        if due_at is not None:
            payload["due_at"] = due_at
        created = (await self._request("POST", "/api/tasks", json=payload)).json()
        # F.2 verlangt source="hermes", die eingefrorene D.4-Anlageroute setzt
        # aber immer source="local". Der dokumentierte Hermes-Pfad ist der
        # bedingungslose PATCH ohne base_version (D.4/D.9) — damit wird die
        # Anlage nachgezogen, ohne das Backend zu aendern.
        try:
            flipped = (
                await self._request("PATCH", f"/api/tasks/{created['id']}", json={})
            ).json()
        except BackendError as exc:
            raise BackendError(
                exc.code, exc.message, exc.retryable, partial={"task": created}
            ) from None
        return flipped

    async def update_task(self, task_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Bedingungsloses Ueberschreiben ueber den Hermes-Pfad (D.4/D.9).

        Bewusst ohne ``base_version``: MCP-Schreibzugriffe gewinnen immer.
        """
        response = await self._request("PATCH", f"/api/tasks/{task_id}", json=fields)
        return response.json()

    async def delete_task(self, task_id: str) -> None:
        await self._request("DELETE", f"/api/tasks/{task_id}")

    async def add_workout(
        self,
        *,
        activity_type: str,
        start_time: str,
        duration_s: int,
        distance_m: float | None = None,
        calories: int | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Legt eine Garmin-Aktivitaet ueber das Backend an (WP-P2-5, D16).

        Der MCP-Server schreibt nie selbst nach Garmin: das Backend delegiert an
        `garmin-sync` `POST /write`. Liefert das normalisierte Workout-Objekt
        (mit `activity_id`). Fehlt das Objekt, gilt die Antwort als ungueltig.
        """
        payload: dict[str, Any] = {
            "activity_type": activity_type,
            "start_time": start_time,
            "duration_s": duration_s,
        }
        if distance_m is not None:
            payload["distance_m"] = distance_m
        if calories is not None:
            payload["calories"] = calories
        if title is not None:
            payload["title"] = title
        response = await self._request("POST", "/api/garmin/workouts", json=payload)
        workout = (response.json() or {}).get("workout")
        if not isinstance(workout, dict):
            raise BackendError(
                "BACKEND_UNAVAILABLE",
                "Backend liefert kein Workout-Objekt",
                retryable=True,
            )
        return workout
