#!/usr/bin/env bash
# Uruchamia WMS API i frontend (Linux/macOS). Użycie: scripts/dev.sh [port]
set -euo pipefail
PORT="${1:-8000}"
cd "$(dirname "$0")/.."
source .venv/bin/activate
[ -f var/wms.db ] || python -m simulator.generate --seed 42 --size medium
uvicorn wms.main:create_app --factory --port "$PORT" &
WMS_PID=$!
trap 'kill $WMS_PID' EXIT
cd frontend
[ -d node_modules ] || npm install
VITE_API_URL="http://localhost:$PORT" npm run dev
