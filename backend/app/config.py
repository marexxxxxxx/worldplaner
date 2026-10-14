"""Backend-Konfiguration (Spezifikation A.4): alle Werte aus Env, keine Konstanten im Code."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Backend / DB
    sqlite_path: str = "/data/dashboard.db"
    backend_port: int = 8000

    # Eingebaute Widgets (Abschnitt C): Ordner mit `widgets/*/manifest.json`.
    # Leer -> vom Repo-/Container-Wurzelverzeichnis abgeleitet (`/widgets`).
    widgets_dir: str = ""

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

    # Garmin-Schreibpfad (WP-P2-5, D16): Forward-Ziel des `garmin-sync`-Write-
    # Endpunkts. Interner Compose-Name (Port 8002), kein Host-Port. Die Werte
    # werden von `services/garmin_write.py` direkt aus der Env gelesen; die
    # Settings spiegeln den dokumentierten Vertrag aus `.env.example`.
    garmin_sync_write_url: str = "http://garmin-sync:8002"
    garmin_sync_write_timeout_seconds: float = 10.0
    # Spiegel der garmin-sync-Env (Compose/G.5); wird vom Adapter gelesen.
    garmin_sync_mock: bool = False

    # Baikal-Kalender (WP-P2-1, D12/D13): Pull-Adapter, HTTP Basic.
    baikal_url: str = ""
    baikal_username: str = ""
    baikal_password: str = ""
    baikal_cache_ttl_seconds: int = 600
    baikal_days_ahead: int = 7
    baikal_timeout_seconds: int = 10

    # Grocy-Bestand (WP-P2-2): Pull-Adapter, `GROCY-API-KEY`.
    grocy_url: str = ""
    grocy_api_key: str = ""
    grocy_cache_ttl_seconds: int = 600
    grocy_timeout_seconds: int = 10

    # Mealie-Wochenplan (WP-P2-3): Pull-Adapter, Bearer-Token.
    mealie_url: str = ""
    mealie_api_token: str = ""
    mealie_cache_ttl_seconds: int = 600
    mealie_days_ahead: int = 7
    mealie_timeout_seconds: int = 10

    # Widget-Installation (WP-P2-4, D18): Zielordner im Volume `widgets-data`
    # sowie die Zip-Haertungsgrenzen (10 MiB / 50 MiB).
    widgets_install_dir: str = "/data/widgets"
    widgets_max_zip_bytes: int = 10485760
    widgets_max_uncompressed_bytes: int = 52428800


@lru_cache
def get_settings() -> Settings:
    return Settings()
