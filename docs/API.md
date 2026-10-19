# WMS API (REST)

Pełna, generowana dokumentacja OpenAPI: **`http://localhost:8000/docs`** (Swagger UI) i `/openapi.json`.
Uwierzytelnianie: `Authorization: Bearer <JWT>`. Błędy: `{"error": "...", "reason": "...", "details"?: {...}}`;
błąd schematu: `{"error": "SCHEMA_VALIDATION", "reason": "invalid_arguments", "fields": [{"loc": [...], "type": "..."}]}`.
Wszystkie ciała żądań mają `extra="forbid"`.

## Uwierzytelnianie

| Metoda | Ścieżka | Dostęp | Opis |
|---|---|---|---|
| POST | `/auth/login` | publiczny | `{username, password}` → JWT użytkownika (rola operator / kierownik_zmiany) |
| POST | `/auth/token` | publiczny | `client_credentials` dla usług (`client_id: mcp-wms`); scope'y nadaje WMS |
| GET | `/auth/me` | każdy token | tożsamość, rola, efektywne scope'y |

## Role i scope'y

| Scope | operator | kierownik_zmiany | agent_service (mcp-wms) |
|---|:-:|:-:|:-:|
| `orders:read`, `inventory:read`, `layout:read`, `workers:read`, `kpis:read`, `waves:read`, `slotting:read`, `forecast:read` | ✓ | ✓ | ✓ |
| `waves:propose`, `waves:release`, `routes:propose`, `slotting:propose`, `audit:append`, `reports:write` | | | ✓ |
| `approvals:request`, `approvals:read_own` | ✓ | ✓ | ✓ |
| `reports:read`, `sim:control` (sterowanie symulacją wymaga tokenu użytkownika) | ✓ | ✓ | |
| `approvals:read_all`, `approvals:decide`, `slotting:decide`, `audit:read` (tylko token użytkownika) | | ✓ | |

Scope'y z tokenu są przycinane do roli; token usługowy nigdy nie ma scope'ów kierownika (D-004).

## Endpointy

| Metoda | Ścieżka | Scope / rola | Opis |
|---|---|---|---|
| GET | `/health` | — | stan usługi |
| GET | `/orders` | `orders:read` | filtry: `status`, `priority`, `due_before`, `zone`, `limit ≤ 500`, `offset` |
| GET | `/orders/{order_id}` | `orders:read` | zamówienie z liniami; `customer_note` + `note_signals` |
| GET | `/inventory` | `inventory:read` | dokładnie jeden z `sku_id`, `location_id` |
| GET | `/layout` | `layout:read` | strefy, geometria, (opcjonalnie) lokalizacje |
| GET | `/layout/heatmap` | `layout:read` | `metric=rotation|fill` → wartość per lokalizacja |
| GET | `/workers` | `workers:read` | pracownicy, `on_shift` |
| GET | `/sim/workers` | `workers:read` | pozycje na żywo |
| GET | `/kpis` | `kpis:read` | `window=15m|1h|shift|day`: lines/h, OTIF, utylizacja, kolejka pakowania, braki, zatory, alerty |
| GET | `/kpis/timeseries` | `kpis:read` | kubełki czasu: lines/h, braki, wysyłki |
| GET | `/sim/state` | `kpis:read` | zegar, tempo, scenariusz |
| POST | `/sim/control` | `sim:control` (użytkownik) | `start`, `pause`, `speed`, `step`, `reset` |
| GET | `/sim/events` | `kpis:read` | zdarzenia (`pick`, `stockout`, …) z `event_ref` |
| GET | `/waves`, `/waves/{id}` | `waves:read` | fale, przydziały, trasy |
| POST | `/waves` | `waves:propose` | propozycja fali (walidacja serwera, `dry_run`, `idempotency_key`) |
| POST | `/waves/{id}/routes` | `routes:propose` | trasa pracownika (`ortools|s_shape|largest_gap|nearest_neighbor`) |
| POST | `/waves/{id}/release` | `waves:release` | zwolnienie (rezerwacja zapasu, limit/h) |
| POST | `/waves/{id}/discard` | `waves:propose` | wycofanie niezwolnionej propozycji |
| GET | `/routes/active`, `/routes/recent` | `waves:read` | trasy (z łamaną / podsumowanie z S-shape) |
| GET | `/planning/wave-suggestions` | `waves:read` | sugestie fal |
| POST | `/forecast` | `forecast:read` | `{sku_ids ≤ 500, horizon_days 1–28}` |
| GET | `/slotting/analysis` | `slotting:read` | ABC/XYZ, COI, sugerowane przesunięcia |
| GET / POST | `/slotting/proposals` | `slotting:read` / `slotting:propose` | lista / propozycja przesunięć |
| POST | `/slotting/proposals/{id}/decide` | kierownik (`slotting:decide`) | `apply` / `reject` (z ponowną walidacją) |
| POST | `/approvals/order-cancellation` | `approvals:request` | wniosek o anulowanie (zawsze `pending`) |
| POST | `/approvals/inventory-adjustment` | `approvals:request` | wniosek o korektę (zawsze `pending`) |
| GET | `/approvals/mine/{id}` | `approvals:read_own` | status własnego wniosku |
| GET | `/approvals`, `/approvals/{id}` | kierownik (`approvals:read_all`) | kolejka i szczegóły (flagi, źródło, diff, `target_changed`) |
| POST | `/approvals/{id}/decide` | kierownik (`approvals:decide`) | `{decision, comment, pin, expected_version}` |
| GET | `/audit`, `/audit/verify` | kierownik (`audit:read`) | dziennik z filtrami, weryfikacja łańcucha hashy |
| POST | `/internal/audit` | `audit:append` (mcp-wms) | wpis audytu od mcp-wms (hash liczy WMS) |
| GET / POST | `/reports` | `reports:read` / `reports:write` | raporty cykli agenta |

## Przykład

```bash
TOKEN=$(curl -s -X POST localhost:8000/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"operator","password":"<hasło demo z simulator/seed_users.py>"}' | jq -r .access_token)
curl -s "localhost:8000/kpis?window=1h" -H "Authorization: Bearer $TOKEN" | jq '{lines_per_hour, otif, alerts}'
```

Uruchomienie: `uvicorn wms.main:create_app --factory --port 8000`.
