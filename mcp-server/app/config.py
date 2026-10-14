"""MCP-Server worldplaner — Konfiguration ausschliesslich aus Env (Spezifikation F.1).

Kein Geheimnis und kein Verbindungsziel steht als Konstante im Code; die
Cloudflare-Access-Pruefung ist env-gesteuert (F.1 / H.2).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

AUTH_MODES = ("auto", "enforce", "edge")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Backend-REST-API (D3: MCP delegiert Task-CRUD an das Backend).
    backend_url: str = "http://backend:8000"
    backend_timeout_seconds: float = 10.0

    # Streamable-HTTP-Transport (F.1). Im Container bindet der Prozess auf
    # 0.0.0.0; die Einschraenkung auf 127.0.0.1 passiert per Compose-Portmapping.
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8001
    # Komma-getrennte Host-/Origin-Allowlist gegen DNS-Rebinding (F.1).
    mcp_allowed_hosts: str = ""
    mcp_allowed_origins: str = ""

    # Display-Steuerung (J.2): DISPLAY und DISPLAY_OUTPUT kommen aus der Umgebung.
    display: str = ":0"
    display_output: str = "eDP-1"
    display_timeout_seconds: float = 5.0

    # Rate Limiting pro Service-Token (F.1). 0 deaktiviert die Bremse.
    mcp_rate_limit_per_minute: int = 60

    # Cloudflare Access (F.1 / H.2).
    #   auto    -> pruefen, sobald AUD + Team-Domain gesetzt sind (MVP-Default)
    #   enforce -> pruefen; fehlende Konfiguration ist ein Startfehler
    #   edge    -> nicht pruefen (Verantwortung liegt vollstaendig am Edge)
    mcp_auth_mode: str = "auto"
    cf_access_team_domain: str = ""
    cf_access_aud: str = ""

    @property
    def auth_mode(self) -> str:
        mode = (self.mcp_auth_mode or "auto").strip().lower()
        return mode if mode in AUTH_MODES else "auto"

    @property
    def auth_config_present(self) -> bool:
        return bool(self.cf_access_aud.strip() and self.cf_access_team_domain.strip())

    @property
    def auth_enabled(self) -> bool:
        mode = self.auth_mode
        if mode == "edge":
            return False
        if mode == "enforce":
            if not self.auth_config_present:
                raise RuntimeError(
                    "MCP_AUTH_MODE=enforce, aber CF_ACCESS_AUD und/oder "
                    "CF_ACCESS_TEAM_DOMAIN fehlen"
                )
            return True
        return self.auth_config_present


@lru_cache
def get_settings() -> Settings:
    return Settings()
