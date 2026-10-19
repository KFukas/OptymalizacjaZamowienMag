# Definicje KPI (zwracane przez `get_kpis`)

| KPI | Definicja | Jak interpretować |
|---|---|---|
| `lines_picked`, `lines_per_hour` | liczba skompletowanych linii w oknie / długość okna [h] | główny miernik wydajności; spadek przy pełnej obsadzie = wąskie gardło gdzie indziej (pakowanie, braki) |
| `lines_per_hour_per_worker` | lines/h ÷ pracownicy na zmianie | 50–120 to typowy zakres w symulacji; poniżej → pracownicy czekają na fale |
| `distance_per_order_m` | suma dystansów zakończonych tras ÷ liczba zamówień na tych trasach | spada po dobrym batchingu i slottingu; porównuj z `baseline_s_shape_m` |
| `otif` | odsetek wysłanych zamówień: na czas (wysyłka ≤ termin) **i** w komplecie (bez braków) | < 0,95 → priorytet dla zamówień zagrożonych terminem |
| `utilization` | pracownicy w ruchu/kompletacji ÷ pracownicy na zmianie (chwilowo) | > 0,9 ryzyko zatorów; < 0,5 przy otwartych zamówieniach → za mało fal |
| `pack_queue` | zamówienia czekające na stanowisko pakowania | ≥ 6 → alert; nie zwalniaj kolejnych dużych fal, rozważ mniejsze |
| `orders_at_risk` | otwarte zamówienia z terminem < 2 h | planuj je w pierwszej kolejności |
| `orders_overdue` | niezrealizowane po terminie | zgłoś w raporcie |
| `stockouts[]` | zdarzenia braku w oknie (`event_ref`, lokalizacja, SKU, potrzeba, znaleziono) | dowód do wniosku o korektę |
| `congestion_by_aisle` | liczba pracowników w alejce | ≥ 3 → zator; rozdziel strefy między fale |
| `picks_by_aisle` | pobrania na alejkę w oknie | heatmapa rotacji |

## Prognoza

- `wape_backtest` — Weighted Absolute Percentage Error na 14 dniach holdoutu: Σ|y−ŷ| / Σy (niżej = lepiej).
- `baseline_wape_backtest` — to samo dla naiwnego sezonowego (powtórzenie zeszłego tygodnia).
- `model` — wybrany model: `ets_holt_winters`, `naive_seasonal`, `mean_28d` (popyt sporadyczny).
- WAPE > 0,6 → prognoza mało wiarygodna; nie przenoś SKU tylko na jej podstawie.

## Zysk tras

`gain_vs_s_shape_pct = (S-shape − algorytm) / S-shape × 100`. Wartość ujemna oznacza, że wybrany algorytm jest
gorszy niż S-shape — przelicz trasę z `ortools`.
