# Kiedy który algorytm

## Trasy (`compute_pick_route.algorithm`)

| Algorytm | Kiedy | Uwagi |
|---|---|---|
| `ortools` (domyślny) | zawsze, gdy liczy się dystans | TSP na grafie alejek, start od nearest neighbour, Guided Local Search ~1 s; nigdy gorszy od NN; w benchmarku −15…−27% dystansu vs S-shape |
| `largest_gap` | gęste pobrania w wielu alejkach, gdy potrzebna przewidywalna trasa dla pracownika | heurystyka „po luce”: skrajne alejki w całości, pośrednie z przodu i z tyłu |
| `s_shape` | punkt odniesienia; bardzo gęste pobrania (prawie każde przęsło) | każda alejka z pobraniem przechodzona w całości |

## Batching (wewnątrz `propose_wave`)

Serwer dzieli zamówienia na kursy wózka (objętość, max zamówień) i przydziela je pracownikom (LPT po czasie).
Zamówienie nigdy nie jest dzielone między kursy. Ty wpływasz na wynik doborem zamówień i pracowników:
- grupuj zamówienia z tych samych stref (mniej alejek na kurs),
- pilne (ekspresy, termin < 2 h) w osobnej, małej fali z kilkoma pracownikami,
- strefa C (chłodnia/ADR) — lepiej w osobnej fali, jeśli zamówień jest dużo.

## Fale

- jedna fala = jeden termin przewoźnika (10:00, 14:00, 18:00, 21:00) + ewentualne zamówienia z późniejszych terminów,
- wielkość fali ≈ pracownicy × pojemność wózka × 2 kursy,
- nie łącz w jednej fali pracowników o różnych godzinach końca zmiany, jeśli cut-off jest blisko.

## Prognoza i slotting

- prognoza: ETS/Holt-Winters dla SKU z regularnym popytem; średnia 28 dni dla sporadycznych (wybór przez backtest),
- slotting: klasa A + niskie COI → lokalizacje najbliżej pakowania, poziomy 1–2; przesuwaj tylko do wolnych,
  kompatybilnych lokalizacji (chłodnia ↔ chłodnia, ADR ↔ ADR); nie ruszaj lokalizacji z aktywnych fal,
- najpierw kilka przesunięć o największym `est_gain_m_per_day` — kierownik akceptuje je ręcznie.
