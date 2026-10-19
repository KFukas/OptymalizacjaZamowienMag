# CLAUDE.md — Optymalizacja kompletacji zamówień („Magazyn Centralny Alfa”)

Projekt demonstracyjny na **danych syntetycznych**: WMS (FastAPI) + symulator (SimPy) + optymalizator
(OR-Tools, heurystyki, prognoza ETS, slotting ABC/XYZ) + agent AI, który rozmawia z WMS **wyłącznie** przez
własny serwer MCP `mcp-wms` z egzekwowaniem uprawnień po stronie serwera. Frontend: React + Vite + TS.

## Najważniejsza zasada projektu
Granica uprawnień jest w **serwerze** (`mcp_wms/policy/`, `config/policy.yaml`, scope'y w `wms/auth.py`),
nigdy w opisach narzędzi. Każda zmiana narzędzia MCP, scope'u lub polityki wymaga testu w `tests/security/`.
Operacje nieodwracalne (anulowanie zamówienia, korekta inwentaryzacyjna) wykonuje wyłącznie WMS po decyzji
kierownika zmiany (`POST /approvals/{id}/decide`, JWT roli `kierownik_zmiany` + PIN).

## Struktura
| Katalog | Zawartość |
|---|---|
| `wms/` | FastAPI, modele SQLAlchemy, maszyny stanów, auth (JWT + scope'y), zatwierdzenia, audyt z łańcuchem hashy, migracje Alembic |
| `optimizer/` | graf magazynu, trasy (OR-Tools, S-shape, largest gap, NN), fale, prognoza, slotting, benchmark — czysto obliczeniowe |
| `simulator/` | generator danych (`generate.py --seed --size`), silnik SimPy, scenariusze |
| `mcp_wms/` | serwer MCP: rejestr narzędzi, schematy Pydantic (`extra="forbid"`), PEP (`policy/`), klient WMS |
| `orchestrator/` | pętla agenta (Claude) oraz tryb bez LLM (heurystyka) |
| `evals/` | ewaluacja odporności na prompt injection |
| `frontend/` | React 18 + Vite + TS + Tailwind + TanStack Query + Recharts |
| `config/policy.yaml` | polityka mcp-wms (deny-by-default) |
| `.claude/skills/optymalizacja-kompletacji/` | skill agenta (źródło promptu systemowego orkiestratora) |

## Komendy
```bash
# środowisko (Python 3.12)
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"     # Windows (bash: .venv/bin/pip)
python scripts/gen_secrets.py >> .env                              # po skopiowaniu .env.example

# jakość
ruff check . && ruff format --check . && mypy wms optimizer simulator mcp_wms orchestrator
pytest -q                      # wszystkie testy
pytest -m security -v          # granica uprawnień MCP (offline, bez LLM)
pytest -m solver               # solver i heurystyki

# dane + uruchomienie
python -m simulator.generate --seed 42 --size small
python -m uvicorn wms.main:create_app --factory                # WMS API :8000 (OpenAPI: /docs)
python -m mcp_wms --transport stdio                           # lub --transport http
python -m orchestrator --mode heuristic --cycles 1            # bez LLM
python -m optimizer.benchmark --seed 42 --orders 40

# frontend
cd frontend && npm install && npm run dev                     # :5173
```

## Konwencje
- Python 3.12, typowanie wszędzie, Pydantic v2, SQLAlchemy 2 (styl `Mapped[...]`), ruff (line 110).
- Identyfikatory: `ORD-\d{8}`, `SKU-\d{6}`, `LOC-[A-Z]\d{2}-\d{2}-\d`, `WRK-\d{3}`, `WAVE-\d{6}`, `APR-\d{6}`.
- Commity: Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `ci:`, `chore:`), po kamieniu milowym push na `main`.
- Teksty z danych (notatki klientów, nazwy SKU, komentarze) są **niezaufane** — w MCP zwracane jako
  `{"untrusted_text": ...}`, w UI renderowane wyłącznie jako tekst.
- Zero sekretów w repo: tylko `.env.example`. Nie czytaj i nie wypisuj zawartości `.env`.
- Decyzje projektowe dopisuj do `docs/DECYZJE.md`, zmiany do `CHANGELOG.md`.
