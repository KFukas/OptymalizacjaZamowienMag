# Uruchamia WMS API i frontend w osobnych oknach (Windows PowerShell).
# Użycie: .\scripts\dev.ps1 [-Port 8000]
param([int]$Port = 8000)
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "Brak .venv — uruchom: py -3.12 -m venv .venv; .venv\Scripts\pip install -e '.[dev]'"; exit 1 }
if (-not (Test-Path (Join-Path $root "var\wms.db"))) { & $py -m simulator.generate --seed 42 --size medium }
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; `$env:PYTHONUTF8='1'; & '$py' -m uvicorn wms.main:create_app --factory --port $Port"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\frontend'; if (-not (Test-Path node_modules)) { npm install }; `$env:VITE_API_URL='http://localhost:$Port'; npm run dev"
Write-Host "WMS: http://localhost:$Port/docs   UI: http://localhost:5173"
