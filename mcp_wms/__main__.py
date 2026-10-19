"""Uruchomienie: `python -m mcp_wms --transport stdio|http`.

stdio: tożsamość klienta z `MCP_CLIENT_TOKEN` (domyślnie = wartość `MCP_AGENT_TOKEN`, rola planner_agent).
http:  każde żądanie musi nieść `Authorization: Bearer <token klienta>`.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

from mcp_wms.config import REPO_ROOT, McpSettings
from mcp_wms.server import build_server


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Serwer MCP mcp-wms")
    ap.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    args = ap.parse_args(argv)
    load_dotenv(REPO_ROOT / ".env")
    # Logi wyłącznie na stderr — stdout należy do protokołu MCP (stdio).
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    settings = McpSettings()
    if args.transport == "stdio" and not settings.mcp_client_token:
        settings.mcp_client_token = os.environ.get("MCP_AGENT_TOKEN", "")
    server = build_server(settings)
    server.run("stdio" if args.transport == "stdio" else "streamable-http")


if __name__ == "__main__":
    main()
