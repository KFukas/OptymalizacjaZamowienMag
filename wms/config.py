"""Konfiguracja WMS (zmienne środowiskowe / .env)."""

from __future__ import annotations

import logging
import secrets
from functools import lru_cache

from pydantic import PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./var/wms.db"
    jwt_secret: str = ""
    jwt_ttl_minutes: int = 480
    service_token_ttl_minutes: int = 60
    approval_ttl_minutes: int = 240
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Sekret klienta usługowego mcp-wms (client_credentials). Scope'y tego klienta są zdefiniowane
    # w SERVICE_CLIENTS poniżej — klient nie może ich rozszerzyć żądaniem.
    wms_mcp_client_secret: str = ""

    # Limity domenowe (obrona w głąb — egzekwowane w WMS niezależnie od mcp-wms).
    max_adjustment_delta_without_evidence: int = 20
    max_adjustment_delta_absolute: int = 500
    adjustment_window_hours: int = 24
    max_approval_requests_per_hour: int = 20
    max_wave_releases_per_hour: int = 12
    max_wave_proposals_per_hour: int = 60
    pin_max_failures: int = 5

    # Opcjonalny zewnętrzny serwis optymalizatora (docker); bez niego obliczenia są w procesie.
    optimizer_url: str | None = None

    # Symulacja: tick w sekundach czasu rzeczywistego.
    sim_tick_seconds: float = 0.5
    sim_autostart: bool = False

    _ephemeral_secret: str | None = PrivateAttr(default=None)

    def effective_jwt_secret(self) -> str:
        if self.jwt_secret:
            return self.jwt_secret
        # Tryb dev bez .env: losowy sekret na czas życia procesu (tokeny nie przeżyją restartu).
        if self._ephemeral_secret is None:
            log.warning("JWT_SECRET nie ustawiony — używam efemerycznego sekretu (tylko dev).")
            self._ephemeral_secret = secrets.token_urlsafe(48)
        return self._ephemeral_secret


@lru_cache
def get_settings() -> Settings:
    return Settings()
