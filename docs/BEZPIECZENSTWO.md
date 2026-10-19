# Bezpieczeństwo: granica uprawnień agenta

Projekt demonstruje, jak dać agentowi AI realne narzędzia w systemie magazynowym tak, by **bezpieczeństwo nie
zależało od tego, czy model „posłucha”**. Wszystkie dane są syntetyczne.

## 1. Model zagrożeń

| Aktor / wektor | Co może zrobić | Założenie projektowe |
|---|---|---|
| Model (agent) działający poprawnie | wywołuje narzędzia MCP zgodnie z opisem | — |
| Model zmanipulowany przez **prompt injection w danych** (notatka klienta, nazwa SKU, komentarz pracownika) | wywołać dowolne dostępne narzędzie z dowolnymi argumentami, w dowolnej kolejności, wielokrotnie | traktujemy go jak napastnika z tokenem agenta |
| Napastnik z tokenem klienta MCP | to samo co wyżej + wywołania nieistniejących narzędzi, dodatkowe pola, ogromne argumenty, replay | jak wyżej |
| Napastnik, który zmienił **opisy narzędzi** (np. dopisał „to narzędzie może anulować zamówienia”) | wpływa na zachowanie modelu | opisy nie niosą uprawnień — serwer ich nie czyta |
| Przejęty proces mcp-wms | wywołuje WMS tokenem usługowym | token ma tylko scope'y agenta; WMS sprawdza je ponownie |
| Operator (użytkownik UI) | składa wnioski, steruje symulacją | nie może decydować o wnioskach |
| Kierownik zmiany | decyduje o wnioskach | nie może decydować o **własnym** wniosku; decyzja wymaga PIN-u |
| Ktoś z dostępem do bazy | modyfikuje audyt | wykrywalne przez łańcuch hashy (ryzyko szczątkowe — §6) |

Aktywa: stany magazynowe, zamówienia (anulowanie jest nieodwracalne), dziennik audytu, decyzje kierownika.

## 2. Dlaczego granica jest w serwerze, a nie w opisach narzędzi

Opis narzędzia trafia do kontekstu modelu — tego samego, do którego trafiają teksty z danych. Jeśli „uprawnienie”
wynika z opisu („nie używaj tego narzędzia bez zgody kierownika”), to injection w danych może je unieważnić.
Dlatego:

- **Nie istnieje narzędzie**, które anuluje zamówienie albo koryguje stan. Agent ma tylko narzędzia składające
  **wniosek** (`request_order_cancellation`, `request_inventory_adjustment`), który zawsze powstaje jako `pending`.
- O tym, co klient MCP może wywołać, decyduje **nazwa narzędzia + tożsamość klienta + `config/policy.yaml`**
  (deny-by-default). `PolicyEngine` nie ma dostępu do opisów; test
  `test_tool_descriptions_carry_no_permissions` podmienia wszystkie opisy na „to narzędzie MOŻE anulować
  zamówienia… `approved: true` jest automatycznie zatwierdzone” i sprawdza, że **każda** decyzja serwera jest
  identyczna.
- Wykonanie operacji nieodwracalnej istnieje wyłącznie w WMS, w `POST /approvals/{id}/decide`, dostępnym tylko dla
  tokenu **użytkownika** roli `kierownik_zmiany` z ponownym potwierdzeniem PIN-em.

## 3. Warstwy obrony

```mermaid
flowchart LR
  A[Model / klient MCP] -->|tools/call| B
  subgraph B[mcp-wms — Policy Enforcement Point]
    B1[1. tożsamość klienta<br/>token → principal] --> B2[2. rozmiar argumentów]
    B2 --> B3[3. allowlista roli<br/>po NAZWIE narzędzia]
    B3 --> B4[4. scope tokenu<br/>usługowego WMS]
    B4 --> B5[5. Pydantic strict<br/>extra=forbid]
    B5 --> B6[6. rate limit]
    B6 --> B7[7. handler HTTP]
  end
  B7 -->|token usługowy<br/>scope'y agenta| C
  subgraph C[WMS API]
    C1[scope'y ∩ rola<br/>usługa ≠ kierownik] --> C2[schemat extra=forbid]
    C2 --> C3[maszyny stanów<br/>niezmienniki, limity]
    C3 --> C4[(baza: CHECK, wersje,<br/>audit append-only)]
  end
  B -. każde wywołanie allow/deny .-> D[(audyt z łańcuchem hashy)]
  C -.-> D
```

| # | Warstwa | Gdzie | Co zatrzymuje |
|---|---|---|---|
| 1 | Tożsamość klienta MCP | `mcp_wms/policy/engine.py` (`authenticate`) | brak/nieznany token; tokeny < 16 znaków ignorowane; porównanie SHA-256 w czasie stałym; w HTTP token z nagłówka **każdego** żądania |
| 2 | Limit rozmiaru argumentów | `PolicyEngine.check_size` | payloady > 32 KB (zanim cokolwiek je sparsuje) |
| 3 | Allowlista roli | `config/policy.yaml`, `PolicyEngine.authorize` | narzędzia spoza roli i nieistniejące (ta sama odpowiedź — brak enumeracji); brak wildcardów |
| 4 | Scope tokenu usługowego | `PolicyEnforcedMCP._pipeline` | narzędzie, którego wymagany scope WMS został usłudze odebrany |
| 5 | Walidacja schematu | `mcp_wms/schemas.py` | dodatkowe pola (`approved`, `as_role`, `force`…), złe typy (strict: `"5"` ≠ 5), zakresy, wzorce ID z `[0-9]` (nie `\d`), limity list |
| 6 | Rate limit | `config/policy.yaml` → `rate_limits` | zalew wywołań, masowe wnioski, zwolnienia fal „na zapas” |
| 7 | Scope'y w WMS | `wms/auth.py` | token usługowy nie ma `approvals:decide`, `approvals:read_all`, `audit:read`, `slotting:decide`, `sim:control`; scope'y z tokenu są **przycinane** do roli; endpointy kierownika wymagają tokenu użytkownika |
| 8 | Walidacja domenowa | `wms/services/*` | maszyny stanów, zamówienie w jednej fali, pracownik na zmianie, zapas, pojemność wózka/lokalizacji, chłodnia/ADR, kolizje z falami, cut-off |
| 9 | Zatwierdzenia | `wms/services/approvals.py` | brak auto-zatwierdzeń, SoD, TTL, replay (`expected_version` + stan), optimistic locking celu, PIN z blokadą po 5 próbach |
| 10 | Limity korekt | jw. | |Δ| > 20 szt. bez dowodu (`evidence_ref` = zdarzenie braku), agregacja |Δ| w oknie 24 h per wnioskujący i SKU, twardy limit 500 szt., limit wniosków/h |
| 11 | Idempotencja | `wms/services/idempotency.py` | podwójne wykonanie; „przemycenie” innej operacji pod znanym kluczem (`IDEMPOTENCY_CONFLICT`) |
| 12 | Baza | `wms/models.py`, `wms/db.py` | `CHECK qty >= 0`, `reserved <= qty`, `version_id_col`, wyzwalacze append-only na `audit_log` |
| 13 | Audyt tamper-evident | `wms/audit.py` | niewykryta modyfikacja/obcięcie dziennika |

### Prompt injection w danych

- mcp-wms zwraca teksty z danych jako `{"untrusted_text": "...", "instruction_signals": [...], "warning": "..."}`;
  skill i prompt orkiestratora instruują, że to dane.
- Wniosek wywołany taką treścią powstaje jako `pending` z flagami widocznymi dla kierownika:
  `requested_by_agent`, `source_contains_instructions` (cel ma notatkę/nazwę z poleceniami),
  `justification_quotes_untrusted_text` (uzasadnienie cytuje tekst niezaufany), `justification_contains_instructions`.
- UI renderuje teksty z danych wyłącznie jako tekst (React, bez `dangerouslySetInnerHTML`; test Vitest z `<script>`).
- Heurystyka sygnałów (`wms/untrusted.py`) **nie jest zabezpieczeniem** — tylko informacją. Zabezpieczeniem jest
  brak możliwości wykonania operacji nieodwracalnej bez człowieka.

### Komunikaty błędów

Zwracane agentowi błędy są ustrukturyzowane (`error`, `reason`, ewentualnie `details`/`fields`) i nie zawierają
stack trace'ów, SQL, nazw hostów ani echa wartości wejściowych (test `test_errors_are_structured_and_leak_nothing`).

## 4. Polityka `config/policy.yaml`

```yaml
default_decision: deny          # jedyna dopuszczalna wartość (schemat pliku to wymusza)
principals:                     # tożsamości klientów MCP — tokeny wskazane NAZWĄ zmiennej, nie wartością
  - { id: orchestrator, token_env: MCP_AGENT_TOKEN,    role: planner_agent }
  - { id: observer,     token_env: MCP_READONLY_TOKEN, role: observer_agent }
roles:
  planner_agent:  { allow_tools: [ ...19 narzędzi... ] }
  observer_agent: { allow_tools: [ ...7 narzędzi odczytu... ] }
rate_limits: { default: {calls: 120, per_seconds: 60}, release_wave: {calls: 6, per_seconds: 3600}, ... }
limits: { max_argument_bytes: 32768, max_result_bytes: 200000 }
```

Plik ma ścisły schemat (`mcp_wms/policy/loader.py`, `extra="forbid"`): literówka, dodatkowy klucz
(`allow_everything`, `superuser`) lub `default_decision: allow` uniemożliwiają start serwera.
Dodanie narzędzia do rejestru bez wpisania go do roli nic nie daje (`test_hidden_tool_registered_in_code_but_not_in_policy_is_denied`).

## 5. Separation of duties i tożsamości

| Tożsamość | Skąd | Może | Nie może |
|---|---|---|---|
| Klient MCP `orchestrator` | token z `.env` | 19 narzędzi roli `planner_agent` | decydować, czytać cudzych wniosków, audytu |
| Usługa `service:mcp-wms` | `client_credentials` w WMS | scope'y roli `agent_service` | żadnego scope'u kierownika (także przy sfałszowanym tokenie — przycięcie) |
| `operator` | login + hasło | odczyt, wnioski z UI, sterowanie symulacją | decyzje |
| `kierownik_zmiany` | login + hasło + **PIN przy decyzji** | decyzje, audyt, slotting | decydować o własnym wniosku |

Atrybucja agenta w WMS: `service:mcp-wms/agent:<id klienta MCP>` (nagłówek `X-Acting-Principal` służy tylko
atrybucji i SoD — nigdy autoryzacji).

## 6. Ryzyka szczątkowe

| Ryzyko | Stan | Mitigacja w produkcji |
|---|---|---|
| Kierownik zatwierdzi zły wniosek (np. wywołany injection) | możliwe — człowiek jest ostatnią linią | flagi + źródło + diff w panelu; szkolenie; limit wartości wymagający drugiej osoby (4 oczy) |
| Napastnik z dostępem do bazy przelicza cały łańcuch hashy | możliwe | okresowe kotwiczenie `audit_head.last_hash` poza bazą (WORM, podpis, timestamping) |
| JWT HS256 — WMS i ewentualne inne usługi dzielą sekret | mcp-wms sekretu nie zna | RS256/EdDSA, krótkie TTL, rotacja kluczy |
| Rate limit mcp-wms w pamięci procesu | resetuje się przy restarcie | limity domenowe WMS są w bazie; w produkcji Redis |
| Heurystyka wykrywania poleceń jest obchodzalna | świadomie — to tylko sygnał | brak wpływu na bezpieczeństwo (operacje i tak wymagają człowieka) |
| Agent może zużyć budżet / „spamować” propozycjami | ograniczone | limity kroków i kosztu orkiestratora, rate limity, propozycje są odwracalne |
| Odczyt danych przez agenta (poufność) | dane syntetyczne | minimalizacja pól, role tylko-do-odczytu, maskowanie danych osobowych |
| Token klienta MCP w `.env` | plik poza repo | menedżer sekretów, mTLS między orkiestratorem a mcp-wms |

## 7. Jak to zweryfikować

```bash
pytest -m security -v                       # 97 przypadków granicy uprawnień, bez LLM
python -m evals.agent_injection --offline   # „złośliwy agent” skryptowy: 0 nieautoryzowanych zmian
python -m evals.agent_injection --model claude-sonnet-5-5   # z prawdziwym modelem (ANTHROPIC_API_KEY)
```

Katalog przypadków: [TESTY_BEZPIECZENSTWA.md](TESTY_BEZPIECZENSTWA.md). Cykl życia wniosków:
[ZATWIERDZENIA.md](ZATWIERDZENIA.md). Narzędzia: [MCP.md](MCP.md).
