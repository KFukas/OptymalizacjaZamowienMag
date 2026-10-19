# Wszystkie kontrole jakości (Windows PowerShell): lint, typy, testy backendu, granica uprawnień, frontend.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\ruff.exe check .; if (-not $?) { exit 1 }
.\.venv\Scripts\ruff.exe format --check .; if (-not $?) { exit 1 }
.\.venv\Scripts\mypy.exe wms optimizer simulator mcp_wms orchestrator evals; if (-not $?) { exit 1 }
.\.venv\Scripts\python.exe -m pytest -q; if (-not $?) { exit 1 }
.\.venv\Scripts\python.exe -m evals.agent_injection --offline --report-dir var\evals; if (-not $?) { exit 1 }
Set-Location frontend
npm run lint; if (-not $?) { exit 1 }
npm test; if (-not $?) { exit 1 }
npm run build
