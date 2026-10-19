"""Orkiestrator agenta.

    python -m orchestrator --mode heuristic --cycles 1                 # bez LLM
    python -m orchestrator --mode llm --cycles 3 --every-sim-min 15    # Claude, co 15 min czasu symulacji
    python -m orchestrator --mcp-url http://127.0.0.1:8765/mcp ...     # do działającego mcp-wms (HTTP)

Domyślnie uruchamia mcp-wms jako proces potomny (stdio). Wszystkie wywołania narzędzi trafiają do
`var/orchestrator/tool_calls.jsonl` (i do audytu w WMS).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from orchestrator.heuristic import HeuristicAgent
from orchestrator.llm_agent import LlmAgent
from orchestrator.mcp_link import REPO_ROOT, McpLink, connect_http, connect_stdio


async def _sim_now(link: McpLink) -> datetime | None:
    is_error, body = await link.call("get_kpis", {"window": "15m"})
    if is_error or "sim_now" not in body:
        return None
    return datetime.fromisoformat(body["sim_now"])


async def run(args: argparse.Namespace) -> None:
    log_path = REPO_ROOT / "var" / "orchestrator" / "tool_calls.jsonl"
    ctx: AbstractAsyncContextManager[McpLink]
    if args.mcp_url:
        ctx = connect_http(args.mcp_url, os.environ.get("MCP_AGENT_TOKEN", ""), log_path)
    else:
        ctx = connect_stdio(log_path)
    async with ctx as link:
        agent: HeuristicAgent | LlmAgent = (
            HeuristicAgent(link) if args.mode == "heuristic" else LlmAgent(link)
        )
        for cycle in range(1, args.cycles + 1):
            start = await _sim_now(link)
            result = await agent.run_cycle()
            print(
                json.dumps(
                    {"cycle": cycle, "mode": args.mode, **_summary(result)}, ensure_ascii=False, default=str
                )
            )
            if cycle == args.cycles:
                break
            while args.every_sim_min and start is not None:
                await asyncio.sleep(args.poll_s)
                now = await _sim_now(link)
                if now is None or (now - start).total_seconds() >= args.every_sim_min * 60:
                    break


def _summary(result: object) -> dict:
    d = dict(result.__dict__)
    d.pop("tool_calls", None)
    d.pop("final_text", None)
    return d


def main(argv: list[str] | None = None) -> None:
    load_dotenv(REPO_ROOT / ".env")
    ap = argparse.ArgumentParser(description="Orkiestrator agenta planującego kompletację")
    ap.add_argument("--mode", choices=["heuristic", "llm"], default="heuristic")
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument(
        "--every-sim-min", type=int, default=15, help="odstęp między cyklami w minutach symulacji"
    )
    ap.add_argument("--poll-s", type=float, default=2.0)
    ap.add_argument("--mcp-url", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    Path(REPO_ROOT / "var").mkdir(exist_ok=True)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
