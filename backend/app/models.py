"""Pydantic-Schemas (Eingabevalidierung mit extra='forbid') und die einheitliche
Fehlerform der REST-API (Spezifikation D.0)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --- Fehlerform (D.0) -------------------------------------------------------

CODES = {
    "VALIDATION_ERROR": 400,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "FORBIDDEN": 403,
    "INTERNAL": 500,
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
