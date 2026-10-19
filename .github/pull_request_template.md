## Co zmienia ten PR?

## Typ zmiany
- [ ] feat
- [ ] fix
- [ ] test
- [ ] docs
- [ ] ci / chore

## Wpływ na granicę uprawnień
- [ ] Nie dotyczy
- [ ] Zmienia `config/policy.yaml`, scope'y WMS lub narzędzia MCP — opisz poniżej i dodaj test w `tests/security/`

## Checklista
- [ ] `ruff check .` i `mypy` przechodzą
- [ ] `pytest` przechodzi (w tym `pytest -m security`)
- [ ] `npm run build` i `npm test` w `frontend/` przechodzą (jeśli dotyczy)
- [ ] Brak sekretów w diffie (`git diff --cached`)
- [ ] Zaktualizowano dokumentację (`docs/`) i `CHANGELOG.md`
