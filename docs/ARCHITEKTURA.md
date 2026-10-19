# Architektura

## Komponenty

```mermaid
flowchart TB
  subgraph UI[Frontend — React + Vite]
    M[Mapa] --- F[Fale] --- K[Wąskie gardła] --- P[Panel kierownika] --- A[Audyt]
  end
  subgraph WMS[WMS API — FastAPI, jedyne źródło prawdy]
    R[Routery REST<br/>auth, catalog, planning,<br/>approvals, audit, sim] --> S[Serwisy domenowe<br/>fale, trasy, slotting,<br/>wnioski, KPI]
    S --> DB[(PostgreSQL / SQLite)]
    SIM[Silnik SimPy<br/>wątek w procesie WMS] --> DB
    S --> OPTC[optimizer_client]
  end
  OPT[optimizer<br/>OR-Tools, heurystyki,<br/>ETS, ABC/XYZ — bezstanowy]
  MCP[mcp-wms<br/>PolicyEnforcedMCP + PEP]
  ORCH[Orkiestrator<br/>LLM: Claude / heurystyka]
  CC[Claude Code<br/>+ skill]
  UI -->|REST, JWT użytkownika| R
  ORCH -->|MCP stdio / HTTP<br/>token klienta MCP| MCP
  CC -->|MCP stdio<br/>.mcp.json| MCP
  MCP -->|REST, token usługowy<br/>scope'y agenta| R
  OPTC -->|w procesie lub HTTP<br/>OPTIMIZER_URL| OPT
  ORCH -.->|Anthropic API| CLAUDE[(Claude)]
```

| Komponent | Kod | Odpowiedzialność | Nie robi |
|---|---|---|---|
| WMS API | `wms/` | stan magazynu, walidacja domenowa, auth, wnioski i decyzje, audyt, KPI, sterowanie symulacją | nie ufa żadnemu klientowi |
| Silnik symulacji | `simulator/engine.py` | napływ zamówień, realizacja zwolnionych fal, braki, pakowanie, pozycje | nie podejmuje decyzji planistycznych |
| Generator danych | `simulator/generate.py` | świat syntetyczny z ziarnem, scenariusze | — |
| Optymalizator | `optimizer/` | graf, trasy, batching, sugestie fal, prognoza, slotting, benchmark | brak dostępu do bazy i zapisu |
| mcp-wms | `mcp_wms/` | 19 wąskich narzędzi, PEP, audyt wywołań | brak dostępu do bazy (test AST), brak sekretu JWT |
| Orkiestrator | `orchestrator/` | pętla agenta co N min symulacji, limity, dziennik wywołań | brak innych narzędzi niż MCP |
| Frontend | `frontend/` | wizualizacja, panel kierownika | brak logiki uprawnień (egzekwuje WMS) |

## Strefy zaufania

| Strefa | Elementy | Zaufanie |
|---|---|---|
| Niezaufana | teksty z danych, wynik modelu, argumenty narzędzi | zero — wszystko walidowane |
| Pośrednia | orkiestrator, mcp-wms | ograniczone scope'y; zakładamy możliwość przejęcia |
| Zaufana | WMS + baza | egzekwuje niezmienniki; decyzje nieodwracalne tylko z tokenem kierownika |
| Człowiek | kierownik zmiany | ostatnia linia dla operacji nieodwracalnych |

## Przepływ: cykl planowania

```mermaid
sequenceDiagram
  autonumber
  participant O as Orkiestrator (agent)
  participant M as mcp-wms (PEP)
  participant W as WMS API
  participant S as Symulator
  O->>M: get_kpis / analyze_slotting / forecast_demand
  M->>W: GET /kpis, /slotting/analysis, POST /forecast
  O->>M: suggest_wave_plan
  O->>M: propose_wave(order_ids, worker_ids, cutoff, rationale)
  M->>W: POST /waves (walidacja: stany, zmiany, zapas, wózki, czas)
  W-->>M: WAVE-… validated + plan kursów
  loop dla każdego pracownika
    O->>M: compute_pick_route(ortools)
    M->>W: POST /waves/{id}/routes → OR-Tools + baseline S-shape
  end
  O->>M: release_wave
  M->>W: POST /waves/{id}/release (rezerwacja zapasu, limit/h)
  S->>W: kompletacja tras, zdarzenia pick/stockout, pakowanie
  O->>M: submit_cycle_report (uzasadnienia → UI)
```

## Przepływ: wniosek agenta → decyzja kierownika → wykonanie

```mermaid
sequenceDiagram
  autonumber
  participant S as Symulator
  participant O as Agent
  participant M as mcp-wms
  participant W as WMS
  participant K as Kierownik (UI)
  S->>W: SimEvent stockout EVT-000231 (fizycznie 0, system 14)
  O->>M: get_kpis → stockouts[EVT-000231]
  O->>M: request_inventory_adjustment(counted_qty=0, evidence_ref=EVT-000231)
  M->>M: PEP: rola, scope, schemat, rate limit
  M->>W: POST /approvals/inventory-adjustment (token usługowy)
  W->>W: dowód, limity |Δ|, okno 24 h, flagi, target_version
  W-->>O: APR-000002 status=pending
  Note over O,W: agent NIE ma ścieżki do decyzji (brak narzędzia, brak scope'u)
  K->>W: GET /approvals/APR-000002 (flagi, źródło, dowód, diff, target_changed)
  K->>W: POST /approvals/APR-000002/decide {approve, komentarz, PIN, expected_version}
  W->>W: JWT kierownika + PIN → pending? → wersja wniosku → SoD → TTL
  alt stan celu bez zmian
    W->>W: qty := 0 (jedna transakcja z audytem) → executed
  else stan zmienił się od wniosku
    W->>W: rollback zmian celu → failed (stale_state)
  end
  W->>W: audit_log += wpis (prev_hash → hash)
```

## Dane i czas

- Baza: SQLite (dev/testy, `init_db`) lub PostgreSQL (docker, `alembic upgrade head`). Migracje: `wms/migrations`.
- Dwa zegary (D-005): czas symulacji dla domeny, zegar ścienny dla bezpieczeństwa (TTL, limity, audyt).
- Zapis serializowany blokadą procesu (`wms/deps.py`) + `FOR UPDATE` nagłówka audytu (PostgreSQL).
- Reset świata zachowuje audyt, wnioski i liczniki ID (D-010).

## Wdrożenie (docker compose)

| Usługa | Obraz | Port | Uwagi |
|---|---|---|---|
| `db` | postgres:16-alpine | — | wolumen `pgdata` |
| `optimizer` | `magazyn-alfa/python` | — | `uvicorn optimizer.service:app` |
| `wms-api` | `magazyn-alfa/python` | 8000 | migracje + dane demo przy pierwszym starcie, symulator w procesie |
| `mcp-wms` | `magazyn-alfa/python` | 8765 | streamable HTTP, `Authorization: Bearer` |
| `frontend` | nginx | 5173 | statyczny build Vite |
| `simulator` | `magazyn-alfa/python` | — | profil `tools`: regeneracja danych scenariusza |
| `orchestrator` | `magazyn-alfa/python` | — | profil `agent`: pętla agenta przez mcp-wms (HTTP) |

Decyzje projektowe: [DECYZJE.md](DECYZJE.md). Bezpieczeństwo: [BEZPIECZENSTWO.md](BEZPIECZENSTWO.md).
