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
