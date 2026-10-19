#!/usr/bin/env bash
# Wszystkie kontrole jakości (Linux/macOS).
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
ruff check . && ruff format --check .
mypy wms optimizer simulator mcp_wms orchestrator evals
pytest -q
python -m evals.agent_injection --offline --report-dir var/evals
cd frontend && npm run lint && npm test && npm run build
