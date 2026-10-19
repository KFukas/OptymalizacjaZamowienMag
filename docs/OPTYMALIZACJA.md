# Optymalizacja: trasy, fale, prognoza, slotting

Pakiet `optimizer/` jest czysto obliczeniowy (bez bazy i zapisu), testowany bez LLM (`pytest -m solver`, testy
właściwości `hypothesis`). WMS wywołuje go w procesie albo przez bezstanową usługę `optimizer.service`
(`OPTIMIZER_URL`, docker) — D-015.

## Graf magazynu (`optimizer/layout.py`)

Jeden blok, równoległe alejki (rozstaw 3,2 m), alejka poprzeczna przednia (`y = 0`, punkt pakowania) i tylna.
Przejście między alejkami tylko alejkami poprzecznymi. Odległość:

```
d(p, q) = |y1 − y2|                                        jeśli ta sama alejka
d(p, q) = |x1 − x2| + min((y1 − f) + (y2 − f), (b − y1) + (b − y2))   w pozostałych przypadkach
```

To dokładnie najkrótsza ścieżka w jawnym grafie (`graph_edges`) — test właściwości porównuje z Dijkstrą.
Macierz odległości dla OR-Tools w centymetrach (liczby całkowite).

## Trasy (`optimizer/routing.py`)

| Algorytm | Opis | Łamana |
|---|---|---|
| **S-shape** | każda alejka z pobraniami przechodzona w całości; przy nieparzystej liczbie alejek ostatnia „nawrotem” | wg definicji heurystyki |
| **Largest gap** | skrajne alejki w całości, w pośrednich pomijana największa luka (część pobrań od tyłu, część od przodu) | wg definicji heurystyki |
| **Nearest neighbour** | zawsze najbliższy nieodwiedzony punkt | najkrótsze ścieżki w grafie |
| **OR-Tools** | TSP (RoutingModel), rozwiązanie startowe = NN, Guided Local Search, limit 1 s | najkrótsze ścieżki w grafie |

Założenia: kompletujący stoi w osi alejki (pobiera z obu stron i wszystkich poziomów w jednym punkcie); punkty o
tych samych współrzędnych są łączone; zamówienia są dzielone na kursy wózka **przed** routingiem i nigdy nie są
rozdzielane między kursy (D-012) — porównanie algorytmów dotyczy wyłącznie kolejności odwiedzin.

Gwarancje (testy właściwości, `tests/optimizer/test_routing.py`):
- każda pozycja jest odwiedzona dokładnie raz, trasa zaczyna i kończy się w punkcie pakowania;
- każdy odcinek łamanej leży na grafie (pionowo w osi alejki lub poziomo w alejce poprzecznej);
- długość ≥ dolne ograniczenie (2 × odległość do najdalszego punktu);
- OR-Tools ≤ nearest neighbour (start od NN);
- trasa w jednej alejce ma długość `2 × (x + y_max)` dla wszystkich algorytmów.

### Benchmark

`python -m optimizer.benchmark --seed 42 --orders N --instances 20` — układ `medium` (20 alejek × 50 przęseł),
partie zamówień po 1–4 linie, lokalizacje losowane z rozkładem Pareto (popularne miejsca częściej), jeden kurs.

| Zamówień w partii | S-shape [m] | Largest gap [m] | Nearest neighbour [m] | **OR-Tools [m]** | OR-Tools vs S-shape | Instancje, w których OR-Tools < S-shape |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 650,9 | 499,0 (−23,3%) | 513,0 (−21,2%) | **478,6** | **−26,5%** | 20/20 |
| 20 | 810,8 | 685,2 (−15,5%) | 718,4 (−11,4%) | **644,9** | **−20,5%** | 20/20 |
| 40 | 877,8 | 889,9 (+1,4%) | 848,8 (−3,3%) | **797,3** | **−9,2%** | 20/20 |

Średni czas obliczeń: heurystyki < 5 ms, OR-Tools ~1 s (limit czasu). Wnioski: przy rzadkich pobraniach zysk
z optymalizacji kolejności jest największy; przy gęstych (40 zamówień ≈ pobrania w prawie każdej alejce)
S-shape zbliża się do optimum, a largest gap traci przewagę — zgodnie z literaturą (Roodbergen & de Koster).
OR-Tools wygrywa we wszystkich 60 instancjach.

**Agent vs heurystyka bazowa w symulacji** (scenariusz `rozbieznosc_stanow`, medium, 4 cykle orkiestratora,
20 tras z wielokursowymi partiami po 7–100 zamówień): łączny dystans tras OR-Tools **−18,5%** względem S-shape
dla tych samych kursów (widok „Wąskie gardła” → „Agent vs heurystyka bazowa”). W układzie `small`
(krótkie alejki, 6 alejek) zysk spada do ~6–8%, bo S-shape niewiele traci w małym magazynie.

## Fale (`optimizer/waves.py`, `optimizer/batching.py`, `wms/services/waves.py`)

1. **Sugestie** (`suggest_wave_plan`): zamówienia otwarte (bez flagi `suspicious`) z terminem w horyzoncie 12 h,
   sortowane po terminie przewoźnika i priorytecie; fala = zamówienia najbliższego terminu (+ dopełnienie
   kolejnymi), wielkość = wolni pracownicy × pojemność wózka × 2 kursy (≤ 100 zamówień).
2. **Walidacja i podział** (`propose_wave` w WMS): alokacja zapasu (z uwzględnieniem innych propozycji i
   kwarantanny po brakach), kursy metodą „seed” (pilne zamówienie startuje kurs, dobierane zamówienia dokładające
   najmniej nowych alejek, limit objętości i liczby zamówień), przydział kursów do pracowników LPT z ich prędkością
   i wydajnością, kontrola czasu do `min(cut-off, koniec zmiany)`.
3. **Opcjonalnie CP-SAT** (`cpsat_batches`): przydział zamówień do kursów minimalizujący liczbę par (kurs, alejka)
   — proxy dystansu; test sprawdza, że nie jest gorszy od heurystyki.

## Prognoza popytu (`optimizer/forecast.py`)

Per SKU: backtest na ostatnich 14 dniach dla kandydatów (ETS/Holt-Winters z tłumionym trendem i addytywną
sezonowością tygodniową; naiwny sezonowy; średnia 28 dni), wybór najmniejszego **WAPE**, prognoza 1–28 dni
zwycięskim modelem dopasowanym do całej historii. Popyt sporadyczny (> 50% zer albo średnia < 0,7/dzień) — bez ETS.
Wyniki w cache (SKU, horyzont, ostatni dzień historii).

Jakość na danych `medium` (seed 42, 180 dni historii, horyzont 7 dni, WAPE ważony średnim popytem):

| Zbiór SKU | WAPE wybranego modelu | WAPE baseline (naiwny sezonowy) | Wybrane modele |
|---|---:|---:|---|
| 50 najpopularniejszych | **0,090** | 0,125 | ETS 40, średnia 8, naiwny 2 |
| 200 najpopularniejszych | **0,140** | 0,194 | ETS 141, średnia 43, naiwny 16 |

LightGBM (opcjonalny w specyfikacji) pominięto — bez cech egzogenicznych nie dawał przewagi (D-014).

## Slotting (`optimizer/slotting.py`, `wms/services/planning.py`)

- **ABC** po skumulowanym udziale w popycie z 28 dni (A < 80%, B < 95%, C reszta; pozycja przekraczająca próg
  należy jeszcze do wyższej klasy), **XYZ** po współczynniku zmienności dziennego popytu (X < 0,5, Y < 1, Z ≥ 1).
- **COI** (cube-per-order index) = zajmowana objętość / dzienny popyt — małe COI → najlepsze lokalizacje.
- **Koszt lokalizacji** = 2 × odległość od pakowania + 2,5 m za każdy poziom powyżej 2 (poziomy 1–2 = „złota strefa”).
- **Propozycje**: SKU w kolejności rosnącego COI przenoszone do najtańszych wolnych, kompatybilnych lokalizacji
  (chłodnia ↔ chłodnia, ADR ↔ ADR, objętość i waga), z pominięciem lokalizacji aktywnych fal i oczekujących
  propozycji; zysk = (koszt obecny − koszt docelowy) × dzienny popyt [m/dzień].
- Wykonanie przesunięć tylko po akceptacji kierownika — z ponowną walidacją każdego ruchu w chwili akceptacji.

## Uruchamianie

```bash
pytest -m solver                                   # testy optymalizatora
python -m optimizer.benchmark --seed 42 --orders 20 --instances 20 --out raport.md
uvicorn optimizer.service:app --port 8100          # bezstanowa usługa HTTP (opcjonalnie)
```
