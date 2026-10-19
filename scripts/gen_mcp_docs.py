"""Generuje docs/MCP.md z rejestru narzędzi, schematów Pydantic i config/policy.yaml (źródło prawdy = kod).

python scripts/gen_mcp_docs.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_wms.policy import load_policy  # noqa: E402
from mcp_wms.tools import build_registry  # noqa: E402

EXAMPLES: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    "list_open_orders": ({"filter": {"priority": "express", "zone": "A"}, "limit": 20}, {"count": 3, "orders": [{"order_id": "ORD-00000017", "priority": "express", "due_at": "2026-11-02T10:00:00", "customer_note": {"untrusted_text": "Proszę dzwonić przed dostawą."}, "flags": []}]}),
    "get_kpis": ({"window": "1h"}, {"lines_per_hour": 412.0, "otif": 0.97, "pack_queue": 2, "stockouts": [{"event_ref": "EVT-000231", "location_id": "LOC-A02-14-1", "sku_id": "SKU-000045", "need": 2, "found": 0}], "alerts": []}),
    "propose_wave": ({"order_ids": ["ORD-00000017", "ORD-00000018"], "worker_ids": ["WRK-001"], "cutoff_time": "2026-11-02T10:00:00", "rationale": "Termin 10:00, 2 ekspresy, strefa A."}, {"wave_id": "WAVE-000012", "dry_run": False, "plan": [{"worker_id": "WRK-001", "trips": [["ORD-00000017", "ORD-00000018"]], "est_duration_s": 610, "available_s": 7200}]}),
    "compute_pick_route": ({"wave_id": "WAVE-000012", "worker_id": "WRK-001", "algorithm": "ortools"}, {"distance_m": 214.6, "baseline_s_shape_m": 268.0, "gain_vs_s_shape_pct": 19.93, "finishes_before_cutoff": True}),
    "request_order_cancellation": ({"order_id": "ORD-00000251", "reason_code": "suspected_fraud", "justification": "Flaga 'suspicious' z systemu: nietypowa ilość i zmiana adresu."}, {"request_id": "APR-000001", "status": "pending", "flags": ["order_flagged_suspicious", "requested_by_agent"], "note": "Wniosek zapisany jako OCZEKUJĄCY…"}),
    "request_inventory_adjustment": ({"location_id": "LOC-A02-14-1", "sku_id": "SKU-000045", "counted_qty": 0, "reason_code": "stockout_event", "justification": "Zdarzenie EVT-000231: brak przy kompletacji.", "evidence_ref": "EVT-000231"}, {"request_id": "APR-000002", "status": "pending", "flags": ["large_delta", "requested_by_agent"]}),
}  # fmt: skip

ERRORS = [
    ("POLICY_DENIED", "unauthenticated", "brak / nieznany token klienta MCP"),
    (
        "POLICY_DENIED",
        "tool_not_permitted",
        "narzędzie nie istnieje albo nie jest na allowliście roli (ta sama odpowiedź)",
    ),
    (
        "POLICY_DENIED",
        "service_scope_missing",
        "token usługowy WMS nie ma scope'u wymaganego przez narzędzie",
    ),
    ("POLICY_DENIED", "arguments_too_large", "argumenty > `max_argument_bytes`"),
    ("POLICY_DENIED", "rate_limited", "przekroczony limit wywołań narzędzia (okno przesuwne)"),
    (
        "SCHEMA_VALIDATION",
        "invalid_arguments",
        "pole spoza schematu, zły typ, zakres, format ID; `fields` = [loc, type] bez echa wartości",
    ),
    (
        "DOMAIN_VALIDATION",
        "…",
        "reguła domenowa WMS (np. `cutoff_in_past`, `wave_infeasible`, `evidence_required`, `adjustment_window_limit_exceeded`, `invalid_moves`)",
    ),
    (
        "INVALID_STATE",
        "…",
        "stan nie pozwala na operację (np. `orders_not_open_or_in_other_wave`, `routes_missing`, `order_not_cancellable`, `duplicate_pending_request`, `counted_below_reserved`)",
    ),
    (
        "NOT_FOUND",
        "…_not_found",
        "obiekt nie istnieje lub nie należy do wywołującego (wnioski cudze = nieistniejące)",
    ),
    ("RATE_LIMITED", "…", "limit WMS (wnioski / fale / zwolnienia na godzinę)"),
    (
        "IDEMPOTENCY_CONFLICT",
        "key_reused_with_different_payload",
        "ten sam `idempotency_key` z innym ładunkiem",
    ),
    ("UPSTREAM_ERROR", "wms_unavailable", "WMS niedostępny (audyt trafia do pliku awaryjnego)"),
    ("RESULT_TOO_LARGE", "narrow_the_query", "wynik > `max_result_bytes`"),
]


def _type(schema: dict[str, Any], defs: dict[str, Any]) -> str:
    if "$ref" in schema:
        return schema["$ref"].split("/")[-1]
    if "anyOf" in schema:
        return " \\| ".join(_type(s, defs) for s in schema["anyOf"])
    t = schema.get("type", "")
    if t == "array":
        return f"list[{_type(schema.get('items', {}), defs)}]"
    if "enum" in schema:
        return "enum(" + ", ".join(f"`{e}`" for e in schema["enum"]) + ")"
    if "const" in schema:
        return f"`{schema['const']}`"
    if schema.get("format") == "date-time":
        return "datetime (ISO 8601)"
    return str(t or "object")


def _constraints(schema: dict[str, Any]) -> str:
    parts = []
    for s in [schema, *schema.get("anyOf", [])]:
        for key, label in (
            ("pattern", "wzorzec"),
            ("minimum", "≥"),
            ("maximum", "≤"),
            ("minLength", "min. dł."),
            ("maxLength", "maks. dł."),
            ("minItems", "min. el."),
            ("maxItems", "maks. el."),
            ("maxProperties", "maks. kluczy"),
        ):
            if key in s:
                val = f"`{s[key]}`" if key == "pattern" else s[key]
                parts.append(f"{label} {val}")
        if s.get("type") == "array" and isinstance(s.get("items"), dict):
            for key, label in (("pattern", "wzorzec el."), ("maxLength", "maks. dł. el.")):
                if key in s["items"]:
                    parts.append(
                        f"{label} `{s['items'][key]}`" if key == "pattern" else f"{label} {s['items'][key]}"
                    )
    return "; ".join(dict.fromkeys(parts))


def fields_table(schema: dict[str, Any]) -> list[str]:
    defs = schema.get("$defs", {})
    req = set(schema.get("required", []))
    rows = ["| Pole | Typ | Wymagane | Domyślnie | Ograniczenia |", "|---|---|---|---|---|"]
    for name, prop in schema.get("properties", {}).items():
        default = json.dumps(prop["default"], ensure_ascii=False) if "default" in prop else ""
        rows.append(
            f"| `{name}` | {_type(prop, defs)} | {'tak' if name in req else 'nie'} | {default} | {_constraints(prop)} |"
        )
    for dname, d in defs.items():
        rows.append(
            f"| ↳ `{dname}` | obiekt (`additionalProperties: false`) | | | pola: "
            + ", ".join(
                f"`{k}`: {_type(v, defs)}" + (f" ({_constraints(v)})" if _constraints(v) else "")
                for k, v in d.get("properties", {}).items()
            )
            + " |"
        )
    return rows


def main() -> None:
    reg = build_registry()
    policy = load_policy(ROOT / "config" / "policy.yaml")
    roles_for = {name: [r for r, cfg in policy.roles.items() if name in cfg.allow_tools] for name in reg}
    out = [
        "# mcp-wms — referencja narzędzi",
        "",
        "> Plik generowany: `python scripts/gen_mcp_docs.py` (z `mcp_wms/tools.py`, `mcp_wms/schemas.py`, `config/policy.yaml`).",
        "",
        "Serwer MCP `mcp-wms` jest **jedyną** drogą agenta do WMS. Każde wywołanie przechodzi przez Policy Enforcement",
        "Point (zob. [BEZPIECZENSTWO.md](BEZPIECZENSTWO.md)): tożsamość klienta → rola → allowlista po nazwie → scope tokenu",
        'usługowego → ścisła walidacja schematu (`extra="forbid"`, `strict`, bez koercji typów) → limit wywołań → WMS',
        "(ponowna walidacja scope'ów, stanu i niezmienników) → audyt allow/deny.",
        "",
        "## Uruchomienie i podłączenie",
        "",
        "| Transport | Polecenie | Tożsamość klienta |",
        "|---|---|---|",
        "| stdio | `python -m mcp_wms --transport stdio` (lub `scripts/run_mcp_wms.py`, używany przez `.mcp.json`) | `MCP_CLIENT_TOKEN`, domyślnie `MCP_AGENT_TOKEN` z `.env` |",
        "| streamable HTTP | `python -m mcp_wms --transport http` → `http://127.0.0.1:8765/mcp` | nagłówek `Authorization: Bearer <token>` przy każdym żądaniu |",
        "",
        "Claude Code: plik `.mcp.json` w repo rejestruje serwer `mcp-wms` (stdio). Alternatywnie HTTP:",
        '`claude mcp add --transport http mcp-wms http://127.0.0.1:8765/mcp --header "Authorization: Bearer $MCP_AGENT_TOKEN"`.',
        "Szczegóły: [INSTRUKCJA_URUCHOMIENIA.md](INSTRUKCJA_URUCHOMIENIA.md).",
        "",
        "## Role (config/policy.yaml)",
        "",
        "| Rola | Opis | Narzędzia |",
        "|---|---|---|",
    ]
    for rname, cfg in policy.roles.items():
        out.append(f"| `{rname}` | {cfg.description} | {len(cfg.allow_tools)} |")
    out += [
        "",
        "Narzędzia nieistniejące w rejestrze **i** istniejące, ale spoza allowlisty roli, dają identyczną odpowiedź",
        '`{"error": "POLICY_DENIED", "reason": "tool_not_permitted"}`. `tools/list` pokazuje tylko narzędzia roli.',
        "",
        "## Przegląd narzędzi",
        "",
        "| Narzędzie | Rodzaj | Scope WMS | Role | Limit wywołań |",
        "|---|---|---|---|---|",
    ]
    for name, spec in reg.items():
        rate = policy.rate_for(name)
        kind = {"read": "odczyt", "propose": "propozycja (odwracalna)", "request": "wniosek do kierownika"}[
            spec.kind
        ]
        out.append(
            f"| [`{name}`](#{name}) | {kind} | `{spec.required_scope}` | {', '.join(roles_for[name])} | {rate.calls} / {rate.per_seconds} s |"
        )
    out += [
        "",
        "Brak narzędzi ogólnych (`execute_sql`, `update_entity`, `http_request`, shell) i brak narzędzi wykonujących",
        "operacje nieodwracalne (anulowanie, korekta) — te wykonuje wyłącznie WMS po decyzji kierownika",
        "(zob. [ZATWIERDZENIA.md](ZATWIERDZENIA.md)).",
        "",
        "## Błędy",
        "",
        "Każdy błąd to `isError: true` i ustrukturyzowane `structuredContent` — bez stack trace'ów, SQL, adresów usług",
        "ani echa wartości wejściowych.",
        "",
        "| `error` | `reason` | Kiedy |",
        "|---|---|---|",
        *[f"| `{e}` | `{r}` | {w} |" for e, r, w in ERRORS],
        "",
        "## Narzędzia",
    ]
    for name, spec in reg.items():
        schema = spec.input_model.model_json_schema()
        out += ["", f"### `{name}`", "", spec.description, "",
                f"Rodzaj: **{spec.kind}** · scope WMS: `{spec.required_scope}` · role: {', '.join(roles_for[name])}", ""]  # fmt: skip
        out += fields_table(schema)
        if name in EXAMPLES:
            args, res = EXAMPLES[name]
            out += ["", "Przykład:", "", "```json", json.dumps({"arguments": args}, ensure_ascii=False, indent=2), "```",
                    "", "```json", json.dumps({"result": res}, ensure_ascii=False, indent=2), "```"]  # fmt: skip
    (ROOT / "docs" / "MCP.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("zapisano docs/MCP.md")


if __name__ == "__main__":
    main()
