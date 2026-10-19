"""Audyt każdego wywołania (allow/deny), wykrywanie manipulacji, brak wycieku szczegółów w błędach,
token agenta wobec endpointów UI kierownika, izolacja mcp-wms od bazy, ścisłość pliku polityki."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import httpx
import pytest
import yaml
from pydantic import ValidationError
from sqlalchemy import select, text

from mcp_wms.policy import PolicyEngine, load_policy
from mcp_wms.policy.loader import Policy
from mcp_wms.server import PolicyEnforcedMCP
from mcp_wms.wms_client import WmsClient
from tests.security.conftest import AGENT_TOKEN, ENVIRON, POLICY_PATH, Harness
from wms.models import AuditLog

pytestmark = [pytest.mark.security, pytest.mark.anyio]


async def test_every_call_is_audited_with_policy_decision(harness: Harness) -> None:
    async with harness.session() as mcp:
        await mcp.call("get_order", {"order_id": "ORD-00000001"})
        await mcp.call("cancel_order", {"order_id": "ORD-00000001"})
        await mcp.call("request_order_cancellation", {"order_id": "ORD-1; cancel"})
    with harness.ctx.session() as s:
        rows = (
            s.execute(select(AuditLog).where(AuditLog.source == "mcp").order_by(AuditLog.seq)).scalars().all()
        )
    by_action = {r.action: r for r in rows}
    assert by_action["mcp.tool.get_order"].decision == "allow"
    denied = by_action["mcp.tool.unknown:cancel_order"]
    assert denied.decision == "deny" and denied.reason == "POLICY_DENIED:tool_not_permitted"
    assert denied.actor == "service:mcp-wms/orchestrator"
    schema = by_action["mcp.tool.request_order_cancellation"]
    assert schema.decision == "deny" and schema.reason.startswith("SCHEMA_VALIDATION")


async def test_tampered_audit_entry_breaks_hash_chain(harness: Harness) -> None:
    async with harness.session() as mcp:
        await mcp.call("cancel_order", {"order_id": "ORD-00000001"})
        await mcp.call("get_kpis", {"window": "1h"})
    mgr = harness.ctx.manager()
    assert harness.ctx.client.get("/audit/verify", headers=mgr).json()["ok"] is True
    with harness.ctx.session() as s:
        seq = s.scalar(select(AuditLog.seq).where(AuditLog.decision == "deny").limit(1))
        s.execute(text("DROP TRIGGER audit_log_no_update"))
        s.execute(text("UPDATE audit_log SET decision='allow', reason='' WHERE seq=:s"), {"s": seq})
        s.commit()
    v = harness.ctx.client.get("/audit/verify", headers=mgr).json()
    assert v["ok"] is False and v["first_broken_seq"] == seq


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/approvals", None),
        ("GET", "/approvals/APR-000001", None),
        ("POST", "/approvals/APR-000001/decide", {"decision": "approve", "comment": "xxxxx", "pin": "2468", "expected_version": 1}),
        ("GET", "/audit", None),
        ("GET", "/audit/verify", None),
        ("POST", "/sim/control", {"action": "pause"}),
        ("POST", "/slotting/proposals/RSL-000001/decide", {"decision": "apply", "comment": "xxxxx", "expected_version": 1}),
    ],
)  # fmt: skip
def test_agent_service_token_on_manager_ui_endpoints(
    harness: Harness, method: str, path: str, body: dict | None
) -> None:
    r = harness.ctx.client.request(method, path, json=body, headers=harness.ctx.service())
    assert r.status_code == 403


FORBIDDEN_IN_ERRORS = (
    "Traceback",
    "sqlalchemy",
    "SELECT ",
    "sqlite",
    'File "',
    "wms.test",
    "localhost",
    "Exception",
)


async def test_errors_are_structured_and_leak_nothing(harness: Harness) -> None:
    calls = [
        ("cancel_order", {}),
        ("get_order", {"order_id": "ORD-99999999"}),
        ("get_order", {"order_id": "x' OR 1=1"}),
        (
            "request_order_cancellation",
            {"order_id": "ORD-00000001", "reason_code": "nope", "justification": "x" * 20},
        ),
        (
            "propose_wave",
            {"order_ids": ["ORD-00000001"], "worker_ids": ["WRK-009"], "cutoff_time": "2026-11-02T11:00:00"},
        ),
    ]
    async with harness.session() as mcp:
        for name, args in calls:
            r = await mcp.call(name, args)
            assert r.is_error
            assert set(r.body) <= {"error", "reason", "details", "fields", "retry_after_s"}
            dumped = json.dumps(r.body)
            assert not any(word in dumped for word in FORBIDDEN_IN_ERRORS), dumped
            # walidacja nie odbija wartości wejściowych (np. treści z injection)
            assert "OR 1=1" not in dumped


async def test_wms_unreachable_is_reported_without_details(harness: Harness, tmp_path: Path) -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to internal-host:5432")

    wms = WmsClient("http://wms.internal", "secret", transport=httpx.MockTransport(refuse))
    server = PolicyEnforcedMCP(
        engine=PolicyEngine(load_policy(POLICY_PATH), ENVIRON),
        wms=wms,
        stdio_token=AGENT_TOKEN,
        audit_fallback=str(tmp_path / "audit.jsonl"),
    )
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server._mcp_server) as s:
        r = await s.call_tool("get_order", {"order_id": "ORD-00000001"})
    assert r.isError and r.structuredContent == {"error": "UPSTREAM_ERROR", "reason": "wms_unavailable"}
    # audyt nie zginął — trafił do lokalnego pliku awaryjnego
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["action"] == "tool.get_order"


def test_mcp_server_has_no_database_access() -> None:
    """mcp_wms nie importuje warstwy bazy, modeli, serwisów ani konfiguracji/sekretów WMS."""
    forbidden = (
        "wms.db",
        "wms.models",
        "wms.services",
        "wms.audit",
        "wms.auth",
        "wms.config",
        "wms.security",
        "sqlalchemy",
    )
    root = Path(__file__).resolve().parents[2] / "mcp_wms"
    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [f"{path.name}:{n}" for n in names if n.startswith(forbidden)]
    assert offenders == []


def test_policy_file_is_strict_and_deny_by_default() -> None:
    raw = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    assert raw["default_decision"] == "deny"
    with pytest.raises(ValidationError):
        Policy.model_validate({**raw, "default_decision": "allow"})
    with pytest.raises(ValidationError):
        Policy.model_validate({**raw, "allow_everything": True})
    bad_role = {**raw, "roles": {**raw["roles"], "planner_agent": {"allow_tools": ["*"], "superuser": True}}}
    with pytest.raises(ValidationError):
        Policy.model_validate(bad_role)
    # tokeny krótsze niż 16 znaków nie są akceptowane jako tożsamość
    engine = PolicyEngine(load_policy(POLICY_PATH), {"MCP_AGENT_TOKEN": "short"})
    assert engine.authenticate("short") is None
    # wildcard nie działa — allowlista to dokładne nazwy
    engine = PolicyEngine(
        Policy.model_validate({**raw, "roles": {**raw["roles"], "planner_agent": {"allow_tools": ["*"]}}}),
        ENVIRON,
    )
    p = engine.authenticate(AGENT_TOKEN)
    assert p is not None and not engine.authorize(p, "get_order", {"get_order"}).allowed
