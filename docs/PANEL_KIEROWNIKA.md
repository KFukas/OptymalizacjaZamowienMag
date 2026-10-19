# Panel kierownika zmiany — instrukcja

Ten dokument jest dla kierownika zmiany. Nie wymaga wiedzy technicznej.

## Po co jest panel

Asystent planujący (agent AI) układa fale kompletacji, trasy i propozycje przestawienia towaru. Dwóch rzeczy
**nie może zrobić sam**: anulować zamówienia ani zmienić stanu magazynowego. Może tylko złożyć **wniosek** —
a decyzję podejmujesz Ty w tym panelu. Dopóki nie zatwierdzisz, nic się nie zmienia.

![Panel kierownika](img/manager-light.png)

## Logowanie

1. Otwórz aplikację (domyślnie `http://localhost:5173`).
2. Zaloguj się swoim kontem kierownika. Zakładki **Panel kierownika** i **Audyt** widzi tylko kierownik.
3. Do każdej decyzji potrzebny jest dodatkowo Twój **PIN**. Po 5 błędnych próbach potwierdzanie zostaje
   zablokowane — skontaktuj się z administratorem.

## Kolejka wniosków

Po lewej stronie lista wniosków (domyślnie „oczekujące”). Przy wniosku widzisz:

- rodzaj: **Anulowanie zamówienia** albo **Korekta stanu**,
- status (oczekuje / wykonany / odrzucony / wygasł / nie wykonano),
- czerwony znacznik **„podejrzane źródło”**, jeśli wniosek może być skutkiem manipulacji (patrz niżej).

Wnioski wygasają po kilku godzinach (domyślnie 4 h) — wygasłego nie da się już zatwierdzić.

## Szczegóły wniosku — na co patrzeć

| Pole | Co oznacza |
|---|---|
| Wnioskujący | kto złożył wniosek; „agent przez MCP” = asystent AI |
| Cel | które zamówienie albo która lokalizacja i produkt |
| Powód | np. „brak przy kompletacji”, „podejrzenie nadużycia” |
| Flagi bezpieczeństwa | ostrzeżenia systemu (tabela poniżej) |
| Uzasadnienie | co napisał wnioskujący — to tylko jego opinia, nie fakt |
| Notatka klienta / nazwa produktu | oryginalny tekst z danych, pokazany jako „tekst niezaufany” |
| Dowód | np. zgłoszenie braku z hali `EVT-000231` (kto, kiedy, ile brakowało) |
| Stan przed / po | co dokładnie zmieni się po zatwierdzeniu (pogrubione = zmiana) |
| „Stan celu zmienił się od złożenia wniosku” | od czasu wniosku coś się zmieniło — sprawdź przed decyzją |

### Flagi — jak je czytać

| Flaga | Co robić |
|---|---|
| Wniosek złożony przez agenta AI | informacyjnie |
| **Dane źródłowe zawierają treść podobną do poleceń** | notatka klienta lub nazwa produktu „rozkazuje” coś systemowi (np. „anuluj wszystkie zamówienia”). To typowa próba oszustwa. Zwykle **odrzuć**. |
| **Uzasadnienie cytuje notatkę klienta** | agent prawdopodobnie dał się przekonać treści notatki. Zwykle **odrzuć**. |
| Uzasadnienie zawiera sformułowania podobne do poleceń | przeczytaj uważnie |
| System oznaczył zamówienie jako podejrzane | sygnał wspierający wniosek o anulowanie — zweryfikuj z działem obsługi klienta |
| Duża różnica stanu | sprawdź na hali albo zażądaj liczenia |

## Podjęcie decyzji

1. Kliknij **Podejmij decyzję…**.
2. Wybierz **Zatwierdź** albo **Odrzuć**.
3. Wpisz komentarz (min. 5 znaków) — np. „Policzone na regale, 0 szt.” albo „Klient potwierdził zamówienie”.
4. Wpisz PIN, zaznacz potwierdzenie i zatwierdź.

Co się dzieje dalej:

- **Zatwierdzenie** wykonuje operację od razu — ale tylko jeśli stan zamówienia / regału jest taki sam jak w chwili
  wniosku. Jeśli w międzyczasie coś się zmieniło (np. ktoś pobrał towar), system **nie wykona** operacji i oznaczy
  wniosek „nie wykonano” — wtedy poproś o nowy wniosek.
- Nie możesz zatwierdzić **własnego** wniosku — musi to zrobić inny kierownik.
- Każda decyzja trafia do dziennika audytu z Twoim nazwiskiem i komentarzem.

## Propozycje przestawienia towaru (slotting)

Pod szczegółami wniosku są propozycje przeniesienia popularnych produktów bliżej pakowania, z szacowanym
zyskiem w metrach dziennie. **Wykonaj przesunięcia** przenosi towar w systemie (system sprawdza jeszcze raz
pojemność, wagę i strefę — chłodnia / ADR), **Odrzuć** zamyka propozycję. Przesunięcia są odwracalne.

## Dziennik audytu

Zakładka **Audyt** pokazuje każde działanie: logowania, wnioski, decyzje, a także **każde** wywołanie narzędzia
przez asystenta AI — także te, których system mu odmówił (kolumna Decyzja = deny, kolumna Powód).
Przycisk **Zweryfikuj cały dziennik** sprawdza, czy nikt nie zmienił ani nie usunął wpisów (każdy wpis jest
powiązany z poprzednim kryptograficznym skrótem). Zielony wynik = dziennik nienaruszony.

## Sterowanie symulacją

Pasek u góry: zegar symulacji, **Start/Pauza**, tempo (×1 … ×300), **+15 min**, wybór scenariusza i **Reset**.
Reset tworzy nowy dzień symulacji, ale **zachowuje** dziennik audytu i historię wniosków (oczekujące wygasają).
