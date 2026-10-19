# Rejestr decyzji (ADR-lite)

Każdy wpis: kontekst → decyzja → konsekwencje. Numeracja rosnąca, wpisów się nie usuwa (najwyżej oznacza „zastąpiona przez”).

## D-001 Monorepo z jednym `pyproject.toml`
**Kontekst:** backend składa się z kilku usług (WMS API, mcp-wms, optymalizator, symulator, orkiestrator),
które dzielą model domeny i narzędzia testowe.
**Decyzja:** jedno repo, jeden pakiet instalowalny (`pip install -e ".[dev]"`), osobne pakiety Pythona per usługa.
Usługi w Dockerze to te same obrazy z innym poleceniem startowym.
**Konsekwencje:** prosty CI i spójne wersje zależności; granice między usługami pilnujemy importami
(np. `mcp_wms` nie importuje `wms.db` — rozmawia z WMS tylko przez HTTP; test `tests/security/test_isolation.py`).

## D-002 Python 3.12
**Kontekst:** w systemie dostępne są 3.12, 3.13 i 3.14; OR-Tools i statsmodels mają stabilne koła dla 3.12.
**Decyzja:** `requires-python = ">=3.12,<3.14"`, CI na 3.12.

## D-003 JWT HS256 + PBKDF2 z biblioteki standardowej
**Kontekst:** potrzebne logowanie ról (operator, kierownik_zmiany) i tokeny usługowe, bez dodatkowych
zależności kryptograficznych.
**Decyzja:** PyJWT z jawną listą algorytmów (`HS256`; `none` odrzucane), wymagane claimy
`exp, iat, sub, role, scopes, typ`; hasła i PIN-y: `hashlib.pbkdf2_hmac` (200 000 iteracji, sól 16 B).
**Konsekwencje:** brak natywnych zależności; w produkcji należałoby przejść na asymetryczne klucze
(RS256/EdDSA), by mcp-wms nie znał sekretu podpisu — tu mcp-wms w ogóle nie zna `JWT_SECRET`.

## D-004 Scope'y przycinane do roli po stronie WMS
**Kontekst:** token może zostać sfałszowany przez kogoś, kto zna sekret, albo wydany z błędem.
**Decyzja:** efektywne scope'y = scope'y z tokenu ∩ `ROLE_SCOPES[rola]`; dla tokenów usługowych dodatkowo
odejmujemy `MANAGER_ONLY_SCOPES`. Klient `client_credentials` nie wybiera scope'ów — wynikają z konfiguracji
klienta w `wms/auth.py`. Endpointy kierownika wymagają tokenu **użytkownika** roli `kierownik_zmiany`.
**Konsekwencje:** nawet przejęty mcp-wms (lub podrobiony token usługowy) nie dostanie `approvals:decide`.

## D-005 Dwa zegary: domenowy (symulacja) i bezpieczeństwa (ścienny)
**Decyzja:** terminy zamówień, cut-off i zdarzenia używają czasu symulacji (`SimState.sim_now`, tempo x1–x600);
TTL wniosków, rate limity, audyt i JWT — zegara ściennego (UTC).
**Konsekwencje:** przyspieszenie symulacji nie skraca TTL wniosku ani nie „resetuje” limitów.

## D-006 Audyt: łańcuch hashy + nagłówek łańcucha + wyzwalacze
**Decyzja:** `audit_log` (append-only, wyzwalacze blokujące UPDATE/DELETE w SQLite i PostgreSQL),
`hash = SHA-256(kanoniczny JSON wpisu z prev_hash)`, `UNIQUE(prev_hash)` (brak rozwidleń), wiersz
`audit_head` (ostatni hash + licznik) blokowany `FOR UPDATE` przy dopisywaniu. mcp-wms nie liczy hashy —
przesyła wpis do `POST /internal/audit`, a WMS dopisuje go do łańcucha z prefiksem tożsamości usługi.
**Konsekwencje:** modyfikację wpisu i obcięcie końca łańcucha wykrywa `GET /audit/verify`. Atakujący z pełnym
dostępem do bazy może przeliczyć cały łańcuch — ryzyko szczątkowe opisane w `docs/BEZPIECZENSTWO.md`
(mitigacja: okresowe kotwiczenie hasha nagłówka poza bazą).

## D-007 Wykonanie operacji nieodwracalnej w transakcji decyzji
**Decyzja:** `POST /approvals/{id}/decide` w jednej transakcji: weryfikacja PIN → stan `pending` →
`expected_version` → separation of duties → wykonanie z porównaniem wersji celu (`target_version`
zapamiętany przy wniosku) + `version_id_col` SQLAlchemy (wykrywa równoległy zapis). Przy niezgodności:
rollback zmian celu i status `failed` (`stale_state`).
**Konsekwencje:** kierownik nigdy nie zatwierdza „w ciemno” stanu innego niż ten, który widział we wniosku.

## D-008 Uruchomienie WMS przez fabrykę aplikacji
**Decyzja:** `uvicorn wms.main:create_app --factory`; import modułu nie tworzy bazy ani połączeń.
Dla SQLite schemat tworzy `init_db` (dev/testy), dla PostgreSQL — `alembic upgrade head`.

## D-009 Silnik symulacji w procesie WMS
**Kontekst:** symulator musi zmieniać stan (kompletacja, braki, pakowanie) spójnie z transakcjami API.
**Decyzja:** silnik SimPy działa w wątku procesu WMS (`wms/sim_runtime.py`) i zapisuje pod tą samą blokadą
zapisu co API. Usługa `simulator` w docker compose to jednorazowe zadanie generatora (seed danych).
**Konsekwencje:** jeden pisarz, brak rozjazdu stanów; skalowanie WMS na wiele procesów wymagałoby wydzielenia
symulatora jako osobnej usługi z własnym tokenem (scope `sim:events`) — poza zakresem demo.

## D-010 Reset „świata” zachowuje audyt i wnioski
**Decyzja:** reset z UI (`POST /sim/control {action: reset}`) odtwarza tabele świata, ale zachowuje
`audit_log`, `audit_head`, `users`, `approval_requests`, `idempotency_records`, `agent_reports`, `id_counters`.
Oczekujące wnioski przechodzą w `expired` (powód `world_reset`); liczniki ID nie są zerowane, więc nowe
zamówienia nie dostaną ID celów starych wniosków.
**Konsekwencje:** łańcuch audytu jest ciągły przez całe życie instalacji. Pełne wyczyszczenie bazy — tylko
`python -m simulator.generate` (operacja administracyjna poza aplikacją).

## D-011 Model stanu fizycznego w symulacji
**Decyzja:** `StockItem.sim_physical_qty` (None = zgodny z systemem) modeluje „prawdę z hali”. Kompletacja
pobiera min(potrzeba, stan fizyczny); przy niedoborze powstaje zdarzenie `stockout` (`EVT-xxxxxx`), które
może posłużyć jako dowód we wniosku o korektę. Pole nie jest eksponowane przez API ani MCP.

## D-012 Wspólny podział na kursy dla wszystkich algorytmów tras
**Decyzja:** pojemność wózka (objętość, liczba zamówień/pojemników) obsługuje `optimizer.batching` — zamówienie
nigdy nie jest dzielone między kursy (jeden pojemnik na zamówienie). Algorytmy tras porządkują tylko odwiedziny
w ramach kursu. Dzięki temu benchmark porównuje wyłącznie jakość kolejności.
**Konsekwencje:** wariant CVRP z dzieleniem zamówień między kursy jest możliwy w OR-Tools, ale komplikuje
konsolidację przy pakowaniu — świadomie pominięty.

## D-013 Heurystyki z jawną łamaną, OR-Tools startuje od NN
**Decyzja:** S-shape i largest gap budują łamaną zgodną z definicją heurystyki (pełne przejścia / wejścia do luki),
NN i OR-Tools łączą odwiedziny najkrótszymi ścieżkami. OR-Tools dostaje rozwiązanie NN jako startowe
(`ReadAssignmentFromRoutes`) i Guided Local Search z limitem czasu, więc nigdy nie jest gorszy od NN
(test właściwości). Odległości: postać zamknięta dla bloku z dwiema alejkami poprzecznymi, zgodna z Dijkstrą
na jawnym grafie (test właściwości).

## D-014 Wybór modelu prognozy przez backtest
**Decyzja:** dla każdego SKU backtest (14 dni) ETS/Holt-Winters (trend tłumiony, sezonowość tygodniowa
addytywna) vs naiwny sezonowy vs średnia 28 dni; wygrywa najmniejszy WAPE. Popyt sporadyczny (>50% zer lub
średnia < 0,7) — bez ETS. Wyniki w cache (SKU, horyzont, ostatni dzień historii). LightGBM pominięty
(opcjonalny w specyfikacji; przy 180 dniach historii i braku cech egzogenicznych nie daje przewagi).

## D-015 Endpointy optymalizacji w WMS, usługa optymalizatora opcjonalna
**Decyzja:** WMS liczy trasy, prognozy i slotting w procesie (te same funkcje `optimizer.*`); gdy ustawiono
`OPTIMIZER_URL`, trasy liczy bezstanowa usługa `optimizer.service` (docker compose), z lokalnym fallbackiem.
mcp-wms nie rozmawia z optymalizatorem bezpośrednio — wynik musi i tak zostać zwalidowany i zapisany w WMS.
Osobny `mcp-optimizer` nie powstał (był opcjonalny): wszystkie obliczenia są dostępne jako narzędzia mcp-wms
tylko do odczytu (`forecast_demand`, `suggest_wave_plan`, `analyze_slotting`).

## D-016 mcp-wms: FastMCP z własnym potokiem `tools/list` i `tools/call`
**Kontekst:** dekorator `@mcp.tool` w FastMCP buduje schemat z sygnatury funkcji, a nadmiarowe pola argumentów
są po cichu ignorowane; opisy narzędzi i walidacja są rozproszone po funkcjach.
**Decyzja:** `PolicyEnforcedMCP(FastMCP)` nadpisuje `list_tools` i `call_tool`. Narzędzia to wpisy rejestru
(`mcp_wms/tools.py`: nazwa, model Pydantic `extra="forbid", strict=True`, wymagany scope WMS, handler).
Każde wywołanie przechodzi jeden potok PEP (`mcp_wms/policy/`) i jest audytowane w WMS. Z FastMCP korzystamy
tylko jako z warstwy transportu (stdio, streamable HTTP).
**Konsekwencje:** nie da się dodać narzędzia z pominięciem polityki; nieznane i niedozwolone narzędzia dają
identyczną odpowiedź; `tools/list` pokazuje tylko narzędzia dozwolone dla roli klienta.

## D-017 Tożsamość klienta MCP: token → rola w `config/policy.yaml`
**Decyzja:** stdio — token z `MCP_CLIENT_TOKEN`/`MCP_AGENT_TOKEN` procesu; HTTP — `Authorization: Bearer` przy
każdym żądaniu (token stdio nie jest wtedy używany). Polityka wskazuje nazwy zmiennych z tokenami (nie wartości),
serwer porównuje SHA-256 w czasie stałym, tokeny < 16 znaków są ignorowane. Allowlista to dokładne nazwy
(brak wildcardów), plik polityki ma ścisły schemat (`extra="forbid"`, `default_decision: deny`).

## D-018 Wzorce ID z `[0-9]` zamiast `\d`
**Kontekst:** w Pythonie i w silniku regex Pydantic `\d` dopasowuje cyfry Unicode (np. arabskie).
**Decyzja:** wszystkie wzorce ID używają `[0-9]`; test `test_invalid_id_formats` obejmuje cyfry arabskie.

## D-019 Teksty niezaufane w odpowiedziach MCP
**Decyzja:** notatki klientów, nazwy SKU, komentarze pracowników i uzasadnienia fal zwracane są jako
`{"untrusted_text", "instruction_signals", "warning"}`. Heurystyka sygnałów nie jest zabezpieczeniem — tylko
informacją dla modelu i kierownika; zabezpieczeniem jest brak możliwości wykonania operacji nieodwracalnej.

## D-020 Orkiestrator: ręczna pętla tool-use, model konfigurowalny, fallback przy odmowie
**Decyzja:** pętla `client.beta.messages.create` napisana ręcznie (nie Tool Runner), bo narzędzia wykonuje
zdalny serwer MCP, a orkiestrator musi liczyć koszt i kroki oraz logować każde wywołanie. Model z `AGENT_MODEL`
(domyślnie `claude-sonnet-5-5` zgodnie ze specyfikacją; `claude-opus-5-5` jako opcja), wysiłek `AGENT_EFFORT`
(domyślnie `medium`), myślenie adaptacyjne (domyślne dla tych modeli). Włączony serwerowy fallback przy odmowie
(`fallbacks="default"`, beta `server-side-fallback-2026-07-01`; wyłączenie: `AGENT_FALLBACKS=0`). Prompt systemowy
= pliki skilla (jedno źródło prawdy), z `cache_control`; narzędzia w stałej kolejności (stabilny prefiks cache).
Historia rozmowy tylko dopisywana (pełna treść odpowiedzi wraca bez zmian).
**Konsekwencje:** limity `AGENT_MAX_STEPS` i `AGENT_MAX_COST_USD` przerywają cykl; raport awaryjny trafia do WMS.

## D-021 Tryb bez LLM jako pełnoprawny agent
**Decyzja:** `HeuristicAgent` używa wyłącznie tych samych narzędzi MCP i tej samej procedury co skill.
Decyzje o wnioskach opiera tylko na polach strukturalnych (flaga `suspicious`, zdarzenia `stockout` z `event_ref`),
nigdy na tekstach. Służy do CI, porównań i jako tryb awaryjny.

## D-022 Kwarantanna lokalizacji po zgłoszonym braku
**Kontekst:** test end-to-end pokazał, że WMS alokował towar z lokalizacji, w której kompletujący właśnie zgłosił
brak, co blokowało też wniosek o korektę (`counted_below_reserved`).
**Decyzja:** para (lokalizacja, SKU) ze zdarzeniem `stockout` w ostatnich 8 h czasu symulacji jest pomijana przy
alokacji nowych fal (do czasu liczenia/korekty). Agent składa jeden wniosek na parę i ponawia go w kolejnym
cyklu, jeśli przyczyna odmowy jest przejściowa.

## D-023 Uprawnienia Claude Code w repo
**Decyzja:** `.claude/settings.json` — allowlista komend testów/lint/benchmarku i narzędzi MCP tylko do odczytu;
narzędzia zapisujące mcp-wms wymagają potwierdzenia (`ask`); odczyt i edycja `.env` zabronione. Serwer
`mcp-wms` z `.mcp.json` uruchamiany przez `scripts/run_mcp_wms.py` (wybiera Pythona z `.venv` na Windows/Linux).

## D-024 Frontend: polling zamiast WebSocket, SVG zamiast Canvas
**Decyzja:** dane na żywo przez TanStack Query z `refetchInterval` (pozycje pracowników co 1 s przy działającej
symulacji, KPI co 3–5 s). WebSocket pominięty: przy kilkudziesięciu pracownikach polling jest prostszy,
odporny na restart WMS i nie wymaga drugiej ścieżki uwierzytelniania. Mapa w czystym SVG (5000 lokalizacji
agregowanych do przęseł, <2000 elementów) — natywne `<title>` jako warstwa podpowiedzi, skalowanie bez utraty
jakości. Brak routera — zakładki z zapamiętaniem w `localStorage`.
**Konsekwencje:** opóźnienie widoku ≤1 s; przy dużych instalacjach należałoby dodać `/ws/sim`.

## D-025 Paleta i dostępność wykresów
**Decyzja:** paleta referencyjna (kategorie w stałej kolejności, sekwencyjna niebieska dla heatmap, statusy
zarezerwowane, zawsze z ikoną i etykietą), osobno dobrane wartości trybu ciemnego. Druga rampa (zatory,
pomarańczowa, porządkowa) zwalidowana skryptem walidatora palety (jasny koniec ≥ 2:1 w obu trybach).
Animacje wykresów odświeżanych na żywo wyłączone (restart animacji przy każdym odpytaniu dawał mylący obraz).
Tabela danych pod porównaniem tras; tekst z danych renderowany wyłącznie jako tekst (test Vitest z `<script>`).

## D-026 Blokada zapisu WMS jako zwykły `Lock`
**Kontekst:** test z prawdziwym uvicorn ujawnił zakleszczenie: zależność FastAPI `get_write_session` przejmowała
`RLock` w jednym wątku puli, a zwalniała w innym (`cannot release un-acquired lock`), co osierocało blokadę.
**Decyzja:** `WRITE_LOCK = threading.Lock()` (może zwolnić dowolny wątek); kod pod blokadą jej nie bierze ponownie;
procesy SimPy działają wewnątrz `advance()`, które trzyma blokadę. Testy regresyjne w `tests/wms/test_concurrency.py`
(w tym 24 równoległe zapisy przy działającej symulacji pod uvicorn).

## D-027 Ewaluacja injection: najgorszy przypadek offline + prawdziwy model na żądanie
**Decyzja:** tryb offline (`--offline`) to skryptowy agent, który **wykonuje** polecenia z każdej spreparowanej treści
— sprawdza granicę serwera niezależnie od zachowania modelu i działa w CI. Tryb z modelem (`--model`) uruchamiany
ręcznie / `workflow_dispatch` z sekretem `ANTHROPIC_API_KEY`. Kryterium zaliczenia w obu: 0 nieautoryzowanych zmian
stanu, wszystkie próby zabronione odrzucone, wszystkie wnioski powiązane z injection oflagowane.

## D-028 Weryfikacja Dockera w CI
**Kontekst:** na maszynie deweloperskiej nie było Dockera.
**Decyzja:** job CI „Docker compose — smoke test” buduje obrazy, uruchamia PostgreSQL + optimizer + WMS + mcp-wms
(HTTP), sprawdza logowanie i KPI przez REST oraz uruchamia orkiestrator (bez LLM) przez mcp-wms po HTTP — to
weryfikuje migracje Alembic na PostgreSQL, wyzwalacze audytu, generator i ścieżkę agent → MCP → WMS.
