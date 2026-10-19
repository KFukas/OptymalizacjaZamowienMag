"""Walidator planu fali (offline, przed wywołaniem `propose_wave`).

Użycie:
    python .claude/skills/optymalizacja-kompletacji/scripts/validate_wave_plan.py plan.json
    echo '{"order_ids": [...], "worker_ids": [...], "cutoff_time": "..."}' | python ... -

Sprawdza to samo co schemat MCP (format ID, limity list, brak duplikatów, brak dodatkowych pól) — szybka
informacja zwrotna bez wywołania serwera. Serwer i tak waliduje wszystko ponownie (oraz stan domeny).
Kod wyjścia: 0 = OK, 1 = błędy (wypisane jako JSON).
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime

ALLOWED = {"order_ids", "worker_ids", "cutoff_time", "rationale", "dry_run", "idempotency_key"}
ORDER = re.compile(r"^ORD-[0-9]{8}$")
WORKER = re.compile(r"^WRK-[0-9]{3}$")


def validate(plan: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["plan musi być obiektem JSON"]
    extra = set(plan) - ALLOWED
    if extra:
        errors.append(f"niedozwolone pola: {sorted(extra)}")
    orders = plan.get("order_ids")
    workers = plan.get("worker_ids")
    if not isinstance(orders, list) or not 1 <= len(orders) <= 100:
        errors.append("order_ids: lista 1–100 elementów")
    else:
        errors += [
            f"order_ids: zły format {o!r}" for o in orders if not isinstance(o, str) or not ORDER.match(o)
        ]
        if len(set(orders)) != len(orders):
            errors.append("order_ids: duplikaty")
    if not isinstance(workers, list) or not 1 <= len(workers) <= 20:
        errors.append("worker_ids: lista 1–20 elementów")
    else:
        errors += [
            f"worker_ids: zły format {w!r}" for w in workers if not isinstance(w, str) or not WORKER.match(w)
        ]
        if len(set(workers)) != len(workers):
            errors.append("worker_ids: duplikaty")
    try:
        datetime.fromisoformat(str(plan.get("cutoff_time")))
    except ValueError:
        errors.append("cutoff_time: wymagany czas ISO 8601")
    if len(str(plan.get("rationale", ""))) > 1000:
        errors.append("rationale: maks. 1000 znaków")
    if isinstance(orders, list) and isinstance(workers, list) and workers and len(orders) / len(workers) > 30:
        errors.append(
            "ostrzeżenie: >30 zamówień na pracownika — fala prawdopodobnie niewykonalna przed cut-off"
        )
    return errors


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else "-"
    if src == "-":
        text = sys.stdin.read()
    else:
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
    try:
        plan = json.loads(text)
    except ValueError as exc:
        print(json.dumps({"ok": False, "errors": [f"niepoprawny JSON: {exc}"]}, ensure_ascii=False))
        return 1
    errors = validate(plan)
    print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
