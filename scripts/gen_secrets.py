"""Generuje losowe sekrety do pliku .env (wypisuje na stdout).

Użycie:  python scripts/gen_secrets.py >> .env
"""

import secrets

KEYS = [
    "JWT_SECRET",
    "WMS_MCP_CLIENT_SECRET",
    "MCP_AGENT_TOKEN",
    "MCP_READONLY_TOKEN",
]

if __name__ == "__main__":
    for key in KEYS:
        print(f"{key}={secrets.token_urlsafe(32)}")
