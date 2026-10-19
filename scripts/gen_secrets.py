"""Generuje losowe sekrety do pliku .env.

Użycie:
    python scripts/gen_secrets.py --init     # tworzy .env z .env.example z podstawionymi sekretami (nie nadpisuje)
    python scripts/gen_secrets.py            # wypisuje KEY=wartość na stdout
"""

import secrets
import sys
from pathlib import Path

KEYS = [
    "JWT_SECRET",
    "WMS_MCP_CLIENT_SECRET",
    "MCP_AGENT_TOKEN",
    "MCP_READONLY_TOKEN",
]
ROOT = Path(__file__).resolve().parents[1]


def init_env() -> int:
    target = ROOT / ".env"
    if target.exists():
        print(".env już istnieje — nic nie zmieniam (usuń go, aby wygenerować od nowa).")
        return 1
    lines = []
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0]
        if key in KEYS:
            line = f"{key}={secrets.token_urlsafe(32)}"
        lines.append(line)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Utworzono {target} z losowymi sekretami: {', '.join(KEYS)}")
    return 0


if __name__ == "__main__":
    if "--init" in sys.argv:
        sys.exit(init_env())
    for key in KEYS:
        print(f"{key}={secrets.token_urlsafe(32)}")
