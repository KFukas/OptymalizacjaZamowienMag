"""Demo „dnia pracy”: cykle agenta (przez mcp-wms) na przemian z krokami symulacji.

    python scripts/demo_day.py --cycles 6 --step-min 20 [--mode heuristic|llm] [--approve]

Wymaga działającego WMS (WMS_URL). Krok symulacji wykonuje konto operatora demo (sterowanie symulacją to
uprawnienie operatora, nie agenta). Opcja --approve dodatkowo zatwierdza wnioski o korektę z dowodem i odrzuca
pozostałe kontem kierownika demo — tylko na potrzeby demonstracji panelu (w normalnej pracy robi to człowiek).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orchestrator.heuristic import HeuristicAgent  # noqa: E402
from orchestrator.llm_agent import LlmAgent  # noqa: E402
from orchestrator.mcp_link import connect_stdio  # noqa: E402
from simulator.seed_users import demo_users  # noqa: E402


def login(client: httpx.Client, username: str) -> dict[str, str]:
    user = next(u for u in demo_users() if u.username == username)
    r = client.post("/auth/login", json={"username": user.username, "password": user.password})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def approve_some(client: httpx.Client) -> None:
    user = next(u for u in demo_users() if u.username == "kierownik")
    h = login(client, "kierownik")
    pending = client.get("/approvals", params={"status": "pending"}, headers=h).json()["requests"]
    for req in pending[:3]:
        decision = (
            "approve"
            if req["kind"] == "inventory_adjustment" and "requested_by_agent" in req["flags"]
            else "reject"
        )
        client.post(
            f"/approvals/{req['request_id']}/decide",
            json={
                "decision": decision,
                "comment": "Demo: decyzja kierownika po weryfikacji na hali.",
                "pin": user.pin,
                "expected_version": req["version"],
            },
            headers=h,
        )


async def main() -> None:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=6)
    ap.add_argument("--step-min", type=int, default=20)
    ap.add_argument("--mode", choices=["heuristic", "llm"], default="heuristic")
    ap.add_argument("--approve", action="store_true")
    args = ap.parse_args()
    wms_url = os.environ.get("WMS_URL", "http://localhost:8000")
    with httpx.Client(base_url=wms_url, timeout=120) as client:
        op = login(client, "operator")
        async with connect_stdio(ROOT / "var" / "orchestrator" / "demo_tool_calls.jsonl") as link:
            agent = HeuristicAgent(link) if args.mode == "heuristic" else LlmAgent(link)
            for cycle in range(1, args.cycles + 1):
                res = await agent.run_cycle()
                print(f"[cykl {cycle}] {getattr(res, 'sim_now', '')} fale={getattr(res, 'waves_released', '?')} "
                      f"wnioski={len(getattr(res, 'approval_requests', []))}")  # fmt: skip
                client.post(
                    "/sim/control", json={"action": "step", "minutes": args.step_min}, headers=op
                ).raise_for_status()
                if args.approve:
                    approve_some(client)


if __name__ == "__main__":
    asyncio.run(main())
