from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from simulator.seed_users import seed_users
from tests.factories import build_minimal
from wms.config import Settings
from wms.db import make_engine
from wms.main import create_app

# Testy nie mogą zależeć od lokalnego .env.
os.environ.setdefault("DEMO_MANAGER_PIN", "2468")

TEST_JWT_SECRET = "test-jwt-secret-0123456789-abcdefghijklmnopqrstuvwxyz"
TEST_MCP_SECRET = "test-mcp-client-secret"
MANAGER_PASSWORD = "Alfa-Kierownik-2026"
MANAGER_PIN = "2468"
MANAGER2_PASSWORD = "Alfa-Kierownik2-2026"
MANAGER2_PIN = "1357"
OPERATOR_PASSWORD = "Alfa-Operator-2026"


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "sqlite:///:memory:",
        "jwt_secret": TEST_JWT_SECRET,
        "wms_mcp_client_secret": TEST_MCP_SECRET,
        "approval_ttl_minutes": 60,
        "sim_autostart": False,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@dataclass
class Ctx:
    client: TestClient
    app: object

    def session(self) -> Session:
        return self.app.state.session_factory()  # type: ignore[attr-defined, no-any-return]

    def login(self, username: str, password: str) -> dict[str, str]:
        r = self.client.post("/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def manager(self) -> dict[str, str]:
        return self.login("kierownik", MANAGER_PASSWORD)

    def manager2(self) -> dict[str, str]:
        return self.login("kierownik2", MANAGER2_PASSWORD)

    def operator(self) -> dict[str, str]:
        return self.login("operator", OPERATOR_PASSWORD)

    def service(self, acting: str = "orchestrator-1") -> dict[str, str]:
        r = self.client.post(
            "/auth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": "mcp-wms",
                "client_secret": TEST_MCP_SECRET,
            },
        )
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}", "X-Acting-Principal": acting}


def build_ctx(**settings_overrides: object) -> Ctx:
    settings = make_settings(**settings_overrides)
    engine = make_engine(settings.database_url)
    app = create_app(settings, engine)
    session = app.state.session_factory()
    build_minimal(session)
    seed_users(session, iterations=1000)
    session.commit()
    session.close()
    return Ctx(TestClient(app), app)


@pytest.fixture
def ctx() -> Iterator[Ctx]:
    c = build_ctx()
    with c.client:
        yield c
