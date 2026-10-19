"""Przenośny launcher mcp-wms dla `.mcp.json` (Windows i Linux).

Uruchamia `python -m mcp_wms --transport stdio` interpreterem z `.venv` (jeśli istnieje), dziedzicząc stdin/stdout,
więc działa niezależnie od tego, jaki `python` jest pierwszy w PATH. Konfiguracja (WMS_URL, sekret klienta,
token agenta) jest czytana przez mcp-wms z pliku `.env` w katalogu repozytorium.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def venv_python() -> str:
    for candidate in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


if __name__ == "__main__":
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT)}
    cmd = [venv_python(), "-m", "mcp_wms", "--transport", "stdio", *sys.argv[1:]]
    sys.exit(subprocess.call(cmd, cwd=str(ROOT), env=env))  # noqa: S603 - stała komenda, bez powłoki
