"""Backend-Konfiguration (Spezifikation A.4): alle Werte aus Env, keine Konstanten im Code."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Backend / DB
    sqlite_path: str = "/data/dashboard.db"
    backend_port: int = 8000

    # Bento-Grid (Abschnitt E.6 / D.1)
    grid_columns: int = 12
    grid_row_height_px: int = 96
    grid_gap_px: int = 8

    # Wetter (Abschnitt D.6, Open-Meteo)
    weather_latitude: float = 48.1374
    weather_longitude: float = 11.5755
    weather_timezone: str = "Europe/Berlin"
    weather_cache_ttl_seconds: int = 600

    # Garmin: Bezugsgroesse fuer die stale-Erkennung (Abschnitt D.5)
    garmin_sync_interval_seconds: int = 1200


@lru_cache
def get_settings() -> Settings:
    return Settings()
