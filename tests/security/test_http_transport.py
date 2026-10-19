"""Transport streamable HTTP: tożsamość z nagłówka Authorization, sprawdzana przy KAŻDYM żądaniu."""

from __future__ import annotations

import socket
import threading
import time

import httpx
import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mcp_wms.policy import PolicyEngine, load_policy
from mcp_wms.server import PolicyEnforcedMCP
from mcp_wms.wms_client import WmsClient
from tests.conftest import TEST_MCP_SECRET
from tests.security.conftest import AGENT_TOKEN, ENVIRON, OBSERVER_TOKEN, POLICY_PATH, Harness

pytestmark = [pytest.mark.security, pytest.mark.anyio]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _session_result(url: str, token: str | None) -> tuple[set[str], dict]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with (
        httpx.AsyncClient(headers=headers, timeout=30) as http,
        streamable_http_client(url, http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = {t.name for t in (await session.list_tools()).tools}
        r = await session.call_tool(
            "request_order_cancellation",
            {
                "order_id": "ORD-00000001",
                "reason_code": "customer_request",
                "justification": "Klient prosi o anulowanie.",
            },
        )
        return tools, r.structuredContent or {}


async def test_http_bearer_identity_per_request(harness: Harness) -> None:
    port = _free_port()
    wms = WmsClient("http://wms.test", TEST_MCP_SECRET, transport=httpx.ASGITransport(app=harness.ctx.app))  # type: ignore[arg-type]
    server = PolicyEnforcedMCP(
        engine=PolicyEngine(load_policy(POLICY_PATH), ENVIRON), wms=wms, stdio_token=AGENT_TOKEN, port=port
    )
    config = uvicorn.Config(server.streamable_http_app(), host="127.0.0.1", port=port, log_level="error")
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if uv.started:
            break
        time.sleep(0.05)
    try:
        url = f"http://127.0.0.1:{port}/mcp"
        # Brak nagłówka: token stdio NIE jest używany w HTTP — klient jest anonimowy.
        tools, body = await _session_result(url, None)
        assert tools == set() and body == {"error": "POLICY_DENIED", "reason": "unauthenticated"}
        tools, body = await _session_result(url, "zly-token-0123456789abcdef")
        assert tools == set() and body["reason"] == "unauthenticated"
        tools, body = await _session_result(url, OBSERVER_TOKEN)
        assert "request_order_cancellation" not in tools and body["reason"] == "tool_not_permitted"
        tools, body = await _session_result(url, AGENT_TOKEN)
        assert "request_order_cancellation" in tools and body["status"] == "pending"
    finally:
        uv.should_exit = True
        thread.join(timeout=10)
