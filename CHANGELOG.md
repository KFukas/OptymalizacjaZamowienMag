# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/pl/1.1.0/), wersjonowanie: [SemVer](https://semver.org/lang/pl/).

## [1.0.0] – 2026-10-19

### M7 – Evale, Docker, dokumentacja
- `evals/agent_injection.py`: tryb offline (złośliwy agent skryptowy, w CI) i z prawdziwym modelem
  (`workflow_dispatch` z sekretem); raport `evals/reports/RAPORT_<data>_<tryb>.md`. Wynik offline: 0
  nieautoryzowanych zmian stanu, 37/37 prób zabronionych odrzuconych, 9/9 wniosków z injection oflagowanych.
- Docker: obraz Pythona (jeden dla WMS, mcp-wms, optymalizatora, symulatora, orkiestratora), frontend (nginx),
  `docker-compose.yml` (PostgreSQL, migracje i dane demo przy starcie), smoke test compose w CI.
- Poprawka współbieżności: blokada zapisu WMS przejmowana i zwalniana w różnych wątkach puli FastAPI (D-026)
  + testy regresyjne pod uvicorn.
- Frontend: rampa heatmapy zależna od motywu (w trybie ciemnym mało = ciemne), zatory jako pasek pod alejką,
  bezczynni pracownicy w rzędzie przy pakowaniu.
- Dokumentacja: README, INSTRUKCJA_URUCHOMIENIA, ARCHITEKTURA, BEZPIECZENSTWO, MCP (generowany), ZATWIERDZENIA,
  OPTYMALIZACJA, SYMULACJA, SKILL, API, PANEL_KIEROWNIKA, TESTY_BEZPIECZENSTWA, RAPORT_WYKONANIA; zrzuty ekranu.
- Skrypty: `gen_secrets.py --init`, `dev.ps1`/`dev.sh`, `test.ps1`/`test.sh`, `demo_day.py`, `screenshots.py`,
  `gen_mcp_docs.py`.

### M6 – Frontend
- React 18 + Vite + TypeScript + Tailwind + TanStack Query + Recharts (`frontend/`).
- Mapa SVG: strefy, alejki, regały; warstwy: heatmapa rotacji/zapełnienia, pracownicy na żywo, animowane trasy
  aktywnych fal, strzałki propozycji przesunięć, zatory w alejkach.
- Fale: Gantt (szacowany czas vs cut-off, postęp), tabela z „dlaczego ta fala?”.
- Wąskie gardła: kafle KPI, alerty (ikona + etykieta), linie/h w czasie, pobrania wg alejek,
  porównanie agent vs S-shape, raporty agenta.
- Panel kierownika: kolejka wniosków z flagami bezpieczeństwa, źródłem danych (tekst niezaufany), dowodem,
  diffem stanu; decyzja z komentarzem i PIN-em; akceptacja propozycji slottingu.
- Audyt: filtry, weryfikacja łańcucha hashy. Sterowanie symulacją, tryb jasny/ciemny, paleta odporna na
  daltonizm (walidowana), teksty z danych wyłącznie jako tekst; testy Vitest; job CI.
- WMS: `/kpis/timeseries`, `/routes/recent`; sugestie fal ograniczone horyzontem 12 h; analiza slottingu
  pomija lokalizacje z oczekujących propozycji. Skrypty `scripts/demo_day.py`, `scripts/screenshots.py`.

### M5 – Orkiestrator + skill
- `python -m orchestrator --mode heuristic|llm --cycles N --every-sim-min M [--mcp-url]`: pętla co N minut
  symulacji; połączenie z mcp-wms przez stdio (proces potomny) albo streamable HTTP; dziennik wszystkich wywołań
  narzędzi (`var/orchestrator/tool_calls.jsonl`).
- Agent LLM (Anthropic SDK): narzędzia z `tools/list` mcp-wms, prompt systemowy z plików skilla z cache,
  limity kroków i kosztu, obsługa odmowy i serwerowy fallback modelu, raport cyklu w UI.
- Agent heurystyczny (bez LLM): stan → prognoza → wnioski o korektę z dowodem → fale → trasy OR-Tools →
  zwolnienie → podejrzane zamówienia → slotting → raport.
- Skill `.claude/skills/optymalizacja-kompletacji/` (SKILL.md, references: kpi, algorytmy, przykłady;
  scripts/validate_wave_plan.py), `.mcp.json`, `.claude/settings.json`, launcher `scripts/run_mcp_wms.py`.
- WMS: kwarantanna lokalizacji po zgłoszonym braku przy alokacji fal.

### M4 – mcp-wms: serwer MCP z egzekwowaniem polityki
- 19 wąsko typowanych narzędzi (odczyt, propozycje, wnioski) — bez narzędzi ogólnych i bez operacji
  nieodwracalnych; schematy Pydantic `extra="forbid"`, strict, wzorce ID, zakresy, limity list.
- PEP (`mcp_wms/policy/`): tożsamość klienta (stdio/HTTP Bearer) → rola z `config/policy.yaml` (deny-by-default)
  → allowlista po nazwie → scope tokenu usługowego → walidacja → rate limit → WMS → audyt allow/deny.
- Klient WMS z tokenem usługowym (`client_credentials`) bez scope'ów kierownika; atrybucja `X-Acting-Principal`.
- Teksty z danych jako `untrusted_text` z sygnałami injection; ustrukturyzowane błędy bez szczegółów wnętrza;
  awaryjny lokalny dziennik audytu przy niedostępności WMS.
- `tests/security/`: 97 przypadków (narzędzia ukryte, tożsamość, dodatkowe pola, zakresy, typy, formaty ID,
  wstrzyknięcia, rozmiary, zmienione opisy narzędzi, zatwierdzanie tokenem agenta, podrobione/cudze/wygasłe
  wnioski, replay, wyścig z optimistic locking, rate limit, agregacja korekt, kolejność wywołań, slotting do
  pełnej/niekompatybilnej lokalizacji, manipulacja audytem, token agenta na endpointach kierownika, izolacja
  od bazy, transport HTTP); osobny job CI.

### M3 – Optymalizacja
- Graf magazynu (postać zamknięta + jawny graf zgodny z Dijkstrą), macierz odległości.
- Trasy: OR-Tools (TSP, GLS, start od NN), S-shape, largest gap, nearest neighbour; trasy wielokursowe.
- Podział fali na pracowników i kursy (heurystyka seed + LPT, opcjonalnie CP-SAT), weryfikacja czasu do cut-off.
- Sugestie fal (termin, priorytet, strefy, dostępni pracownicy).
- Prognoza: ETS/Holt-Winters vs naiwny sezonowy vs średnia, backtest WAPE/MAPE, cache.
- Slotting: ABC/XYZ, COI, propozycje przesunięć z zyskiem [m/dzień]; walidacja pojemności, wagi, chłodni/ADR
  i kolizji z aktywnymi falami; zatwierdzenie przez kierownika.
- WMS: `POST /waves`, `/waves/{id}/routes|release|discard`, `/planning/wave-suggestions`, `/forecast`,
  `/slotting/analysis`, `/slotting/proposals`, `/layout/heatmap`, `/reports`; usługa `optimizer.service`.
- Benchmark `python -m optimizer.benchmark`: OR-Tools −26,5% (10 zam.) / −15,6% (30 zam.) dystansu vs S-shape.

### M2 – Generator danych + symulator SimPy
- `python -m simulator.generate --seed --size small|medium|large --scenario`: układ (np. medium: 3 strefy,
  20 alejek, 5000 lokalizacji), 2000 SKU z popularnością Pareto, celowo nieoptymalny slotting startowy,
  180 dni historii sprzedaży (sezonowość tygodniowa, trend, promocje), zmiany i pracownicy, backlog zamówień,
  przypadki prompt injection w notatkach, nazwach SKU i komentarzach.
- Scenariusze: zwykły dzień, Black Friday, brak 2 pracowników, rozbieżność stanów, zamówienie podejrzane,
  notatka z injection.
- Silnik SimPy: napływ Poissona z profilem godzinowym, wykonywanie zwolnionych fal, braki towaru (zdarzenia
  `stockout` jako dowody), kolejka pakowania, przerwy, pozycje pracowników na żywo; tempo x1–x600, pauza, reset.
- API: `/sim/state`, `/sim/control`, `/sim/events`, `/sim/workers`, `/kpis` (lines/h, OTIF, utylizacja,
  kolejka pakowania, braki, zatory, alerty), `/waves` (odczyt), `/routes/active`, `/reports`.

### M1 – Model domeny + WMS API
- Encje SQLAlchemy 2 (strefy, lokalizacje, SKU, zapasy, zamówienia, fale, trasy, propozycje slottingu,
  wnioski o zatwierdzenie, audyt, idempotencja, użytkownicy, historia sprzedaży, zdarzenia symulacji).
- Maszyny stanów (zamówienie, fala, wniosek, propozycja slottingu).
- Auth: JWT (role `operator`, `kierownik_zmiany`, `agent_service`), `client_credentials` dla mcp-wms,
  scope'y przycinane do roli, PIN do ponownego potwierdzenia decyzji.
- Wnioski o anulowanie / korektę z flagami injection, limity (dowód, agregacja w oknie, rate limit),
  decyzja kierownika w jednej transakcji z optimistic locking, TTL, separation of duties, idempotencja.
- Audyt append-only z łańcuchem hashy, weryfikacja `GET /audit/verify`, wyzwalacze DB.
- Migracje Alembic (schemat + wyzwalacze dla SQLite/PostgreSQL), 43 testy.

### M0 – Szkielet
- Struktura repozytorium (pakiety `wms`, `optimizer`, `simulator`, `mcp_wms`, `orchestrator`, `evals`).
- `README.md`, `CLAUDE.md`, `LICENSE` (MIT), `.gitignore`, `.env.example`, `scripts/gen_secrets.py`.
- GitHub Actions (lint + typy + testy), szablony issue (błąd, obejście granicy uprawnień) i PR.
- `docs/DECYZJE.md` — rejestr decyzji.
