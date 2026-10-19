"""Konfiguracja mcp-wms (zmienne środowiskowe / .env)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]


class McpSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    wms_url: str = "http://localhost:8000"
    wms_mcp_client_secret: str = ""
    policy_path: str = str(REPO_ROOT / "config" / "policy.yaml")
    mcp_http_host: str = "127.0.0.1"
    mcp_http_port: int = 8765
    # Token klienta dla transportu stdio (proces uruchamiany przez Claude Code / orkiestrator).
    mcp_client_token: str = ""
    audit_fallback_path: str = str(REPO_ROOT / "var" / "audit_fallback_mcp.jsonl")
    wms_timeout_s: float = 30.0
