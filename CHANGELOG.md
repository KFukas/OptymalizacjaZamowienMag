# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/pl/1.1.0/), wersjonowanie: [SemVer](https://semver.org/lang/pl/).

## [Unreleased]

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
