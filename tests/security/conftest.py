"""Środowisko testów granicy uprawnień: WMS w procesie (ASGI), mcp-wms z prawdziwą polityką, klient MCP
połączony przez transport w pamięci. Żadnego LLM — testy udają złośliwego agenta wprost."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

from mcp_wms.config import REPO_ROOT
from mcp_wms.policy import PolicyEngine, load_policy
from mcp_wms.policy.loader import Policy
from mcp_wms.server import PolicyEnforcedMCP
from mcp_wms.tools import ToolSpec, build_registry
from mcp_wms.wms_client import WmsClient
from tests.conftest import TEST_MCP_SECRET, Ctx, build_ctx

pytestmark = pytest.mark.security

AGENT_TOKEN = "agent-token-for-tests-0123456789abcdef"
OBSERVER_TOKEN = "observer-token-for-tests-0123456789abcdef"
ENVIRON = {"MCP_AGENT_TOKEN": AGENT_TOKEN, "MCP_READONLY_TOKEN": OBSERVER_TOKEN}
POLICY_PATH = REPO_ROOT / "config" / "policy.yaml"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class Result:
    is_error: bool
    body: dict[str, Any]

    @property
    def error(self) -> str | None:
        return self.body.get("error") if self.is_error else None

    @property
    def reason(self) -> str | None:
        return self.body.get("reason") if self.is_error else None


class Harness:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx

    def make_server(
        self,
        token: str | None = AGENT_TOKEN,
        *,
        policy: Policy | None = None,
        registry: dict[str, ToolSpec] | None = None,
    ) -> PolicyEnforcedMCP:
        transport = httpx.ASGITransport(app=self.ctx.app)  # type: ignore[arg-type]
        wms = WmsClient("http://wms.test", TEST_MCP_SECRET, transport=transport)
        engine = PolicyEngine(policy or load_policy(POLICY_PATH), ENVIRON)
        return PolicyEnforcedMCP(engine=engine, wms=wms, registry=registry, stdio_token=token)

    @asynccontextmanager
    async def session(self, token: str | None = AGENT_TOKEN, **kw: Any) -> AsyncIterator[McpCaller]:
        server = self.make_server(token, **kw)
        async with create_connected_server_and_client_session(server._mcp_server) as s:
            yield McpCaller(s, server)


class McpCaller:
    def __init__(self, session: ClientSession, server: PolicyEnforcedMCP) -> None:
        self.session = session
        self.server = server

    async def call(self, name: str, args: dict[str, Any] | None = None) -> Result:
        r = await self.session.call_tool(name, args or {})
        body = r.structuredContent
        if body is None:
            body = json.loads(r.content[0].text) if r.content else {}  # type: ignore[union-attr]
        return Result(bool(r.isError), body)

    async def tool_names(self) -> set[str]:
        return {t.name for t in (await self.session.list_tools()).tools}


@pytest.fixture
def harness() -> AsyncIterator[Harness]:
    ctx = build_ctx()
    with ctx.client:
        yield Harness(ctx)  # type: ignore[misc]


def registry_with_descriptions(text: str) -> dict[str, ToolSpec]:
    reg = build_registry()
    for spec in reg.values():
        spec.description = text
    return reg
