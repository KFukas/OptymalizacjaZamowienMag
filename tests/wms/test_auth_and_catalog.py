from __future__ import annotations

import jwt
import pytest

from tests.conftest import OPERATOR_PASSWORD, TEST_JWT_SECRET, Ctx
from wms.auth import MANAGER_ONLY_SCOPES, ROLE_SCOPES, Role
from wms.security import issue_jwt


def test_login_ok_and_bad(ctx: Ctx) -> None:
    assert (
        ctx.client.post(
            "/auth/login", json={"username": "operator", "password": OPERATOR_PASSWORD}
        ).status_code
        == 200
    )
    r = ctx.client.post("/auth/login", json={"username": "operator", "password": "zle"})
    assert r.status_code == 401
    assert r.json() == {"error": "UNAUTHENTICATED", "reason": "invalid_credentials"}


def test_service_token_has_no_manager_scopes(ctx: Ctx) -> None:
    me = ctx.client.get("/auth/me", headers=ctx.service()).json()
    assert me["role"] == "agent_service"
    assert me["token_type"] == "service"
    assert not MANAGER_ONLY_SCOPES.intersection(me["scopes"])
    assert me["actor_id"] == "service:mcp-wms/agent:orchestrator-1"


def test_agent_role_static_config_has_no_manager_scopes() -> None:
    assert not MANAGER_ONLY_SCOPES.intersection(ROLE_SCOPES[Role.AGENT_SERVICE])


def test_wrong_client_secret(ctx: Ctx) -> None:
    r = ctx.client.post(
        "/auth/token",
        json={"grant_type": "client_credentials", "client_id": "mcp-wms", "client_secret": "x" * 20},
    )
    assert r.status_code == 401


def test_forged_token_with_extra_scopes_is_clamped(ctx: Ctx) -> None:
    """Nawet poprawnie podpisany token usługowy z `approvals:decide` nie da tego scope'u."""
    token = issue_jwt(
        TEST_JWT_SECRET,
        subject="service:mcp-wms",
        role="agent_service",
        scopes=[*ROLE_SCOPES[Role.AGENT_SERVICE], "approvals:decide", "approvals:read_all"],
        token_type="service",
        ttl_minutes=5,
    )
    me = ctx.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert "approvals:decide" not in me["scopes"]
    assert ctx.client.get("/approvals", headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_token_signed_with_other_secret_rejected(ctx: Ctx) -> None:
    token = issue_jwt(
        "inny-sekret" * 4,
        subject="kierownik",
        role="kierownik_zmiany",
        scopes=[],
        token_type="user",
        ttl_minutes=5,
    )
    assert ctx.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_alg_none_rejected(ctx: Ctx) -> None:
    token = jwt.encode(
        {"sub": "kierownik", "role": "kierownik_zmiany", "scopes": ["approvals:decide"], "typ": "user"},
        key="",
        algorithm="none",
    )
    assert ctx.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_user_token_cannot_claim_agent_role(ctx: Ctx) -> None:
    token = issue_jwt(
        TEST_JWT_SECRET, subject="x", role="agent_service", scopes=[], token_type="user", ttl_minutes=5
    )
    assert ctx.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_orders_require_auth(ctx: Ctx) -> None:
    assert ctx.client.get("/orders").status_code == 401


def test_list_and_get_order(ctx: Ctx) -> None:
    h = ctx.operator()
    r = ctx.client.get("/orders", params={"status": "open", "limit": 5}, headers=h)
    assert r.status_code == 200
    assert r.json()["count"] == 5
    o = ctx.client.get("/orders/ORD-00000003", headers=h).json()
    assert o["status"] == "open"
    assert "override_instructions" in o["note_signals"]


@pytest.mark.parametrize("bad", ["ORD-1", "ORD-1; cancel", "../etc/passwd", "ORD-0000000A"])
def test_bad_order_id_rejected(ctx: Ctx, bad: str) -> None:
    r = ctx.client.get(f"/orders/{bad}", headers=ctx.operator())
    assert r.status_code in (404, 422)


def test_inventory_and_layout(ctx: Ctx) -> None:
    h = ctx.operator()
    inv = ctx.client.get("/inventory", params={"sku_id": "SKU-000001"}, headers=h).json()
    assert {i["location_id"] for i in inv["items"]} == {"LOC-A01-01-1", "LOC-B05-07-2"}
    assert ctx.client.get("/inventory", headers=h).status_code == 422
    lay = ctx.client.get("/layout", headers=h).json()
    assert lay["geometry"]["aisle_count"] == 6
    assert len(lay["locations"]) == 6 * 8 * 2


def test_workers_on_shift(ctx: Ctx) -> None:
    ws = ctx.client.get("/workers", headers=ctx.operator()).json()["workers"]
    on = {w["worker_id"]: w["on_shift"] for w in ws}
    assert on["WRK-001"] is True
    assert on["WRK-009"] is False  # zmiana popołudniowa, symulacja o 8:00
