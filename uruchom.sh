#!/usr/bin/env bash
# Uruchamia cały projekt (Linux/macOS): WMS API + symulator, frontend i agenta. Np.: ./uruchom.sh --llm
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "[start] Pierwsze uruchomienie — tworzę środowisko Python 3.12…"
  python3.12 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -e ".[dev]"
fi
PYTHONUTF8=1 exec .venv/bin/python scripts/start_all.py "$@"
