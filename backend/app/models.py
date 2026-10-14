"""Pydantic-Schemas (Eingabevalidierung mit extra='forbid') und die einheitliche
Fehlerform der REST-API (Spezifikation D.0)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --- Fehlerform (D.0) -------------------------------------------------------

CODES = {
    "VALIDATION_ERROR": 400,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "FORBIDDEN": 403,
    "INTERNAL": 500,
    # D17 (spec-phase2): ein fehlgeschlagener Garmin-Schreibvorgang ist ein
    # echter Fehler und wird als 502 gemeldet (kein 401 in der Backend-API).
    "GARMIN_UNAVAILABLE": 502,
    "GARMIN_AUTH_FAILED": 502,
}


class ApiError(Exception):
    """Fehler mit stabilem Code und HTTP-Status (D.0)."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = CODES[code]


def not_found(message: str) -> ApiError:
    return ApiError("NOT_FOUND", message)


def conflict(message: str) -> ApiError:
    return ApiError("CONFLICT", message, retryable=True)


def validation_error(message: str) -> ApiError:
    return ApiError("VALIDATION_ERROR", message)


def forbidden(message: str) -> ApiError:
    return ApiError("FORBIDDEN", message)


def garmin_unavailable(message: str) -> ApiError:
    """Garmin-Backend (`garmin-sync`) nicht erreichbar/fehlerhaft (D17, 502)."""
    return ApiError("GARMIN_UNAVAILABLE", message, retryable=True)


def garmin_auth_failed(message: str) -> ApiError:
    """Garmin-Session ungueltig/abgelaufen; erneuter MFA-Login ist Setup (D17)."""
    return ApiError("GARMIN_AUTH_FAILED", message, retryable=False)


# --- Eingabeschemas ---------------------------------------------------------

_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


class WidgetPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_enabled: bool | None = None
    config: dict | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "WidgetPatch":
        if self.is_enabled is None and self.config is None:
            raise ValueError("is_enabled oder config erforderlich")
        return self


class LayoutCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    widget_id: str = Field(min_length=1)
    tab_id: str = Field(min_length=1)
    x: int = Field(default=0, ge=0)
    y: int = Field(default=0, ge=0)
    w: int = Field(default=2, ge=1)
    h: int = Field(default=2, ge=1)
    config_override: dict = Field(default_factory=dict)


class LayoutUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int | None = Field(default=None, ge=0)
    y: int | None = Field(default=None, ge=0)
    w: int | None = Field(default=None, ge=1)
    h: int | None = Field(default=None, ge=1)
    config_override: dict | None = None


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    priority: Literal["low", "normal", "high"] = "normal"
    due_at: str | None = Field(default=None, pattern=_DATE_PATTERN)


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: Literal["open", "done"] | None = None
    priority: Literal["low", "normal", "high"] | None = None
    due_at: str | None = Field(default=None, pattern=_DATE_PATTERN)
    base_version: int | None = None


class StateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class FocusUpdate(BaseModel):
    """Payload fuer `PUT /api/state/focus` (spec-focus D.1, WP-F1-1).

    `instance_id` ist die `dashboard_layout.id` der Ziel-Instanz — im Fokus-Objekt
    heisst das Feld `instance_id`, waehrend `GET /api/layout` dasselbe Feld `id`
    nennt. Ohne `instance_id` loest das Backend die erste Layout-Instanz des
    Widgets ueber alle Tabs auf.
    """

    model_config = ConfigDict(extra="forbid")

    widget_id: str = Field(min_length=1)
    mode: Literal["highlight", "enlarge", "fullscreen"]
    instance_id: str | None = None


# --- Garmin-Schreibpfad (WP-P2-5) ------------------------------------------


class GarminWorkoutCreate(BaseModel):
    """Payload fuer `POST /api/garmin/workouts` (spec-phase2 D/WP-P2-5).

    Pflicht: `activity_type`, `start_time`, `duration_s`.
    Optional: `distance_m`, `calories`, `title`.
    """

    model_config = ConfigDict(extra="forbid")

    activity_type: str = Field(min_length=1)
    start_time: str = Field(min_length=1)
    duration_s: int = Field(ge=1)
    distance_m: float | None = Field(default=None, ge=0)
    calories: int | None = Field(default=None, ge=0)
    title: str | None = None

    @field_validator("start_time")
    @classmethod
    def _start_time_is_iso8601(cls, value: str) -> str:
        text = value.strip()
        try:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("start_time muss ein ISO-8601-Zeitstempel sein") from None
        return value
