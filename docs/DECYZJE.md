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
