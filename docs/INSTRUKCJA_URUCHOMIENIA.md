# Instrukcja uruchomienia

## Wymagania

| Narzędzie | Wersja | Uwagi |
|---|---|---|
| Python | 3.12 (3.13 też działa) | `py -3.12` na Windows |
| Node.js | 20+ | frontend |
| Git | dowolna aktualna | |
| Docker + Compose v2 | opcjonalnie | wariant z PostgreSQL |
| Klucz API Anthropic | opcjonalnie | tylko tryb agenta LLM i evale z modelem |

## 1. Konfiguracja `.env`

`.env` nie trafia do repozytorium (jest w `.gitignore`). Utwórz go z szablonu `.env.example` z losowymi
sekretami (`JWT_SECRET`, `WMS_MCP_CLIENT_SECRET`, `MCP_AGENT_TOKEN`, `MCP_READONLY_TOKEN`):

```bash
python scripts/gen_secrets.py --init      # PowerShell i bash; nie nadpisuje istniejącego .env
```

Najważniejsze zmienne:

| Zmienna | Znaczenie |
|---|---|
| `DATABASE_URL` | `sqlite:///./var/wms.db` (lokalnie) lub `postgresql+psycopg://…` |
| `JWT_SECRET` | sekret podpisu JWT w WMS (tylko WMS go zna) |
| `WMS_MCP_CLIENT_SECRET` | sekret klienta usługowego mcp-wms (`client_credentials`) |
| `MCP_AGENT_TOKEN`, `MCP_READONLY_TOKEN` | tokeny klientów MCP (rola z `config/policy.yaml`), min. 16 znaków |
| `WMS_URL` | adres WMS dla mcp-wms (domyślnie `http://localhost:8000`) |
| `ANTHROPIC_API_KEY`, `AGENT_MODEL` | agent LLM (domyślnie `claude-sonnet-5-5`, alternatywnie `claude-opus-5-5`) |
| `AGENT_MAX_STEPS`, `AGENT_MAX_COST_USD`, `AGENT_EFFORT` | limity cyklu agenta |
| `CORS_ORIGINS` | adresy frontendu |
| `DEMO_*` | (opcjonalnie) nadpisanie haseł/PIN-ów kont demo |

## 2. Uruchomienie lokalne

### Windows (PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m simulator.generate --seed 42 --size medium            # dane demo (SQLite w var/wms.db)
python -m uvicorn wms.main:create_app --factory --port 8000     # WMS API + symulator
```

W drugim terminalu (frontend):

```powershell
cd frontend; npm install; npm run dev                            # http://localhost:5173
```

W trzecim terminalu (agent bez LLM — uruchamia mcp-wms przez stdio):

```powershell
.\.venv\Scripts\Activate.ps1
python -m orchestrator --mode heuristic --cycles 1
python scripts\demo_day.py --cycles 6 --step-min 20             # kilka cykli + kroki symulacji
```

Skrypty pomocnicze: `scripts\dev.ps1` (WMS + frontend w osobnych oknach), `scripts\test.ps1` (wszystkie testy).

### Linux / macOS (bash)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m simulator.generate --seed 42 --size medium
uvicorn wms.main:create_app --factory --port 8000 &
(cd frontend && npm install && npm run dev) &
python -m orchestrator --mode heuristic --cycles 1
```

Skrypty: `scripts/dev.sh`, `scripts/test.sh`.

### Agent LLM (Claude)

```bash
# w .env: ANTHROPIC_API_KEY=..., opcjonalnie AGENT_MODEL=claude-opus-5-5
python -m orchestrator --mode llm --cycles 3 --every-sim-min 15
```

Uruchom w UI symulację (Start, tempo ×10–×60); orkiestrator wykonuje cykl co 15 minut czasu symulacji. Raporty
z uzasadnieniami decyzji: zakładka **Wąskie gardła** → „Raporty agenta”. Każde wywołanie narzędzia:
`var/orchestrator/tool_calls.jsonl` oraz zakładka **Audyt**.

### Inny port WMS

Jeśli port 8000 jest zajęty: `uvicorn … --port 8010`, w `.env` ustaw `WMS_URL=http://127.0.0.1:8010`, a frontend
uruchom z `VITE_API_URL=http://127.0.0.1:8010 npm run dev`.

## 3. Docker

```bash
python scripts/gen_secrets.py --init
docker compose up --build -d                      # db, optimizer, wms-api, mcp-wms, frontend
docker compose --profile agent up -d orchestrator # agent (heurystyka; AGENT_MODE=llm + klucz dla Claude)
docker compose run --rm simulator --scenario black_friday --size medium   # inny scenariusz (kasuje dane!)
```

- UI: http://localhost:5173, WMS API: http://localhost:8000/docs, mcp-wms: http://localhost:8765/mcp.
- WMS przy pierwszym starcie wykonuje `alembic upgrade head` i generuje dane demo (`SEED_ON_START=1`,
  `SIZE`, `SCENARIO`, `SEED`).
- Porty zmienisz zmiennymi `WMS_PORT`, `MCP_PORT`, `UI_PORT` (frontend budowany z odpowiednim `VITE_API_URL`).
- Działanie compose jest sprawdzane w CI (job „Docker compose — smoke test”).

## 4. Konta demo

Konta (operator oraz dwóch kierowników zmiany z PIN-em do potwierdzania decyzji) tworzy generator danych. Loginy,
domyślne hasła i PIN-y są zdefiniowane w `simulator/seed_users.py` (funkcja `demo_users()`); można je nadpisać
zmiennymi `DEMO_MANAGER_PASSWORD`, `DEMO_MANAGER_PIN`, `DEMO_MANAGER2_PASSWORD`, `DEMO_MANAGER2_PIN`,
`DEMO_OPERATOR_PASSWORD` przed generowaniem danych. Drugi kierownik istnieje po to, by pokazać zasadę
„nie zatwierdzasz własnego wniosku”.

## 5. Podłączenie mcp-wms do Claude Code

Repozytorium zawiera `.mcp.json` (serwer `mcp-wms`, transport stdio, launcher `scripts/run_mcp_wms.py`, który
wybiera Pythona z `.venv`). Warunki: `.venv` z zainstalowanym projektem, `.env` z sekretami, działający WMS.

```bash
claude            # w katalogu repozytorium; przy pierwszym uruchomieniu zaakceptuj serwer z .mcp.json
/mcp              # powinno pokazać: mcp-wms · connected · 19 tools
```

Skill `optymalizacja-kompletacji` jest wykrywany automatycznie z `.claude/skills/` (np. polecenie „zaplanuj fale
na najbliższy termin przewoźnika”). Narzędzia zapisujące wymagają potwierdzenia (`.claude/settings.json`).

Alternatywnie przez HTTP (gdy mcp-wms działa w Dockerze lub `python -m mcp_wms --transport http`):

```bash
claude mcp add --transport http mcp-wms http://127.0.0.1:8765/mcp --header "Authorization: Bearer <MCP_AGENT_TOKEN>"
```

## 6. Podłączenie do Claude Desktop

W `claude_desktop_config.json` (Windows: `%APPDATA%\Claude\`, macOS: `~/Library/Application Support/Claude/`):

```json
{
  "mcpServers": {
    "mcp-wms": {
      "command": "C:\\sciezka\\do\\OptymalizacjaZamowienMag\\.venv\\Scripts\\python.exe",
      "args": ["C:\\sciezka\\do\\OptymalizacjaZamowienMag\\scripts\\run_mcp_wms.py"]
    }
  }
}
```

(na Linux/macOS: `.venv/bin/python` i ścieżka bezwzględna do `scripts/run_mcp_wms.py`). Konfiguracja WMS i token
klienta są czytane z `.env` w katalogu repozytorium.

## 7. Testy

```bash
pytest -q                                   # wszystkie (~185 testów, ~2 min)
pytest -m security -v                       # granica uprawnień MCP (97)
pytest -m solver                            # optymalizator
python -m evals.agent_injection --offline   # ewaluacja injection bez LLM
cd frontend && npm run lint && npm test && npm run build
```

## 8. Rozwiązywanie problemów

| Objaw | Przyczyna / rozwiązanie |
|---|---|
| `Brak danych symulacji — uruchom python -m simulator.generate` | baza pusta — wygeneruj dane |
| `/mcp` w Claude Code: `failed` | brak `.venv` lub `.env`; sprawdź `python scripts/run_mcp_wms.py` ręcznie (powinien czekać na stdin) |
| Narzędzia zwracają `UPSTREAM_ERROR: wms_unavailable` | WMS nie działa lub zły `WMS_URL`; `curl $WMS_URL/health` |
| `POLICY_DENIED: unauthenticated` | `MCP_AGENT_TOKEN` pusty / krótszy niż 16 znaków / inny niż w procesie mcp-wms |
| Frontend: „Błąd połączenia z WMS” | `VITE_API_URL` i `CORS_ORIGINS` muszą wskazywać właściwe adresy |
| `[Errno 10048]` / `address already in use` | port zajęty — użyj innego (sekcja „Inny port WMS”) |
| Decyzja: „Ponowne potwierdzenie zablokowane” | 5 błędnych PIN-ów; zresetuj konta: `python -m simulator.generate …` (kasuje dane) |
| Agent LLM: `Brak ANTHROPIC_API_KEY` | ustaw klucz w `.env` lub użyj `--mode heuristic` |
| `pip install` na Windows: błąd z podpowiedzią „Long Path support” | repozytorium w zbyt długiej ścieżce — sklonuj bliżej korzenia dysku (np. `C:\Projekty\…`) albo włącz obsługę długich ścieżek |
| Polskie znaki w konsoli Windows | `set PYTHONUTF8=1` / `$env:PYTHONUTF8=1` |
