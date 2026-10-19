"""Połączenie orkiestratora z mcp-wms. Orkiestrator nie ma innej drogi do WMS niż ten klient MCP."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from mcp import StdioServerParameters, types
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class ToolCallRecord:
    ts: float
    tool: str
    arguments: dict[str, Any]
    is_error: bool
    result: dict[str, Any]
    duration_ms: float


@dataclass
class McpLink:
    """Cienka warstwa nad ClientSession: wywołania + dziennik wszystkich wywołań narzędzi."""

    session: ClientSession
    log_path: Path | None = None
    calls: list[ToolCallRecord] = field(default_factory=list)

    async def list_tools(self) -> list[types.Tool]:
        return list((await self.session.list_tools()).tools)

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> tuple[bool, dict[str, Any]]:
        args = arguments or {}
        t0 = time.perf_counter()
        r = await self.session.call_tool(name, args)
        body = r.structuredContent
        if body is None:
            text = "".join(c.text for c in r.content if isinstance(c, types.TextContent))
            try:
                body = json.loads(text) if text else {}
            except ValueError:
                body = {"text": text}
        rec = ToolCallRecord(
            time.time(), name, args, bool(r.isError), body, (time.perf_counter() - t0) * 1000
        )
        self.calls.append(rec)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec.__dict__, ensure_ascii=False, default=str) + "\n")
        return bool(r.isError), body


@asynccontextmanager
async def connect_stdio(log_path: Path | None = None) -> AsyncIterator[McpLink]:
    """Uruchamia `python -m mcp_wms --transport stdio` jako proces potomny (konfiguracja z .env)."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_wms", "--transport", "stdio"],
        env={**os.environ, "PYTHONUTF8": "1"},
        cwd=str(REPO_ROOT),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield McpLink(session, log_path)


@asynccontextmanager
async def connect_http(url: str, token: str, log_path: Path | None = None) -> AsyncIterator[McpLink]:
    """Łączy się z działającym mcp-wms (streamable HTTP) — orkiestrator zna wtedy tylko swój token MCP."""
    async with (
        httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=120) as http,
        streamable_http_client(url, http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield McpLink(session, log_path)
