---
name: Obejście granicy uprawnień
about: Zgłoś sposób, w jaki agent / klient MCP mógł wykonać operację, do której nie powinien mieć dostępu
title: "[SECURITY-BOUNDARY] "
labels: security, boundary
---

> Projekt jest demonstracyjny i działa na danych syntetycznych. Jeżeli znalezisko dotyczy
> realnego wdrożenia, nie publikuj szczegółów w issue — opisz tylko klasę problemu.

## Jaka granica została przekroczona?
- [ ] wykonanie operacji nieodwracalnej (anulowanie / korekta) bez decyzji kierownika
- [ ] wywołanie narzędzia spoza allowlisty roli (`config/policy.yaml`)
- [ ] akceptacja argumentów, które powinny być odrzucone (dodatkowe pola, zakresy, formaty)
- [ ] obejście limitu (rate limit, limit korekty, agregacja w oknie)
- [ ] zatwierdzenie własnego wniosku / replay decyzji / podrobiony `request_id`
- [ ] modyfikacja audytu niewykryta przez weryfikację łańcucha hashy
- [ ] wyciek informacji wewnętrznych w komunikatach błędów
- [ ] inne:

## Minimalny przypadek (wywołanie MCP / REST)
```json
{"tool": "...", "arguments": {}}
```

## Oczekiwany wynik
np. `{"error": "POLICY_DENIED", "reason": "..."}`

## Faktyczny wynik / zmiana stanu

## Proponowany test regresyjny
Ścieżka w `tests/security/` i krótki opis (zob. `docs/TESTY_BEZPIECZENSTWA.md`).
