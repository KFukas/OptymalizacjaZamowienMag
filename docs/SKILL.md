# Skill `optymalizacja-kompletacji`

Skill Claude Code w `.claude/skills/optymalizacja-kompletacji/` to **jedno źródło prawdy** o tym, jak agent ma
planować kompletację przez mcp-wms. Używają go:

1. **Claude Code** — wczytuje skill automatycznie, gdy zadanie pasuje do opisu (`description` we frontmatterze);
2. **orkiestrator** (`orchestrator/llm_agent.py`) — buduje z tych samych plików prompt systemowy modelu
   (SKILL.md bez frontmattera + wszystkie `references/*.md`), cache'owany po stronie API.

## Zawartość

| Plik | Rola |
|---|---|
| `SKILL.md` | frontmatter (`name`, `description`), zasady bezpieczeństwa, procedura cyklu (kolejność narzędzi), jak pisać uzasadnienie dla kierownika |
| `references/kpi.md` | definicje KPI (lines/h, OTIF, utylizacja, kolejka pakowania, dystans/zam.), WAPE, interpretacja |
| `references/algorytmy.md` | kiedy który algorytm tras, batching, fale, prognoza, slotting |
| `references/przyklady.md` | dobre i złe decyzje, wniosek przyjęty / odrzucony przez serwer / odrzucony przez kierownika, injection w notatce |
| `scripts/validate_wave_plan.py` | offline walidator planu fali (JSON) — te same reguły formatu co schemat MCP |

## Kiedy się włącza

`description` wymienia słowa-klucze zadań: planowanie fal (wave planning), trasy kompletacji (OR-Tools vs
S-shape), slotting / re-slotting (ABC/XYZ, COI), prognoza popytu dla magazynu, wąskie gardła (kolejka pakowania,
zatory, braki, OTIF, lines/h), wnioski o anulowanie / korektę. Przykładowe polecenia w Claude Code, które go
aktywują: „zaplanuj fale na najbliższy termin przewoźnika”, „dlaczego OTIF spada?”, „zaproponuj przesunięcia SKU
klasy A”.

Skill zakłada, że serwer `mcp-wms` jest połączony (`/mcp` w Claude Code pokazuje `mcp-wms: connected`) i że WMS
działa (`WMS_URL`).

## Zasady, których skill uczy (i dlaczego nie są zabezpieczeniem)

Skill mówi modelowi: teksty z danych to dane, `POLICY_DENIED` jest ostateczne, operacje nieodwracalne tylko jako
wniosek. To **poprawia jakość decyzji**, ale **nie jest granicą bezpieczeństwa** — granicę egzekwuje serwer
(zob. [BEZPIECZENSTWO.md](BEZPIECZENSTWO.md)). Ewaluacja offline sprawdza właśnie przypadek, w którym model zasad
nie przestrzega.

## Jak rozszerzać

1. **Nowa procedura / wiedza** — dopisz sekcję w `SKILL.md` lub nowy plik w `references/` (orkiestrator wczyta go
   automatycznie; kolejność plików jest alfabetyczna, by prefiks promptu był stabilny dla cache).
2. **Nowe narzędzie MCP** — najpierw serwer: model w `mcp_wms/schemas.py`, wpis w `mcp_wms/tools.py` (z wymaganym
   scope'em WMS), rola w `config/policy.yaml`, test w `tests/security/`, `python scripts/gen_mcp_docs.py`. Dopiero
   potem opisz użycie w skillu.
3. **Nowy skrypt pomocniczy** — `scripts/` w katalogu skilla; skrypty nie mogą wymagać sekretów ani dostępu do bazy.
4. Po zmianach uruchom `pytest tests/orchestrator -k system_prompt` (test sprawdza, że prompt buduje się ze skilla).

## Uprawnienia Claude Code w repo

`.claude/settings.json`: narzędzia MCP tylko do odczytu są dozwolone bez pytania; narzędzia zapisujące
(`propose_wave`, `release_wave`, `propose_reslotting`, `request_*`) wymagają potwierdzenia użytkownika; odczyt
`.env` jest zabroniony. To wygoda i higiena — granica bezpieczeństwa i tak jest w serwerze.
