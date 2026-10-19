"""Heurystyczne wykrywanie poleceń w tekstach niezaufanych (notatki klientów, nazwy SKU, komentarze).

To NIE jest zabezpieczenie (heurystykę da się obejść) — służy wyłącznie do oflagowania wniosków
i notatek dla kierownika. Właściwą ochroną jest granica uprawnień: agent nie może wykonać operacji
nieodwracalnej, a każdy wniosek przechodzi przez człowieka.
"""

from __future__ import annotations

import re

_PATTERNS: dict[str, str] = {
    "addresses_agent": r"\b(agen(t|cie|ta)|assistant|asystent|ai|llm|claude|model)\b",
    "imperative_cancel": r"\b(anuluj|cancel|usu[nń]|delete|skasuj|wycofaj)\b",
    "imperative_adjust": r"\b(skoryguj|korekt[aęy]|adjust|zmie[nń] stan|ustaw stan|wyzeruj)\b",
    "override_instructions": r"(zignoruj|ignore|disregard|pomi[nń]|zapomnij).{0,40}(instrukcj|polece|instruction|rules|zasad)",
    "authority_claim": r"\b(kierownik|manager|admin|administrator|system|root)\b.{0,30}(zatwierdzi|approved|pozwala|nakazuje|authori)",
    "approval_spoof": r"(\"?approved\"?\s*[:=]\s*true|as_role|skip_validation|force\s*[:=])",
    "tool_syntax": r"(<\s*/?\s*(system|tool|function|instructions)\b|\btool_use\b|request_order_cancellation|request_inventory_adjustment)",
    "urgency": r"\b(natychmiast|pilnie|immediately|urgent|bez zatwierdzenia|without approval)\b",
}
_COMPILED = {name: re.compile(p, re.IGNORECASE | re.UNICODE) for name, p in _PATTERNS.items()}
_THRESHOLD = 2


def instruction_signals(text: str | None) -> list[str]:
    if not text:
        return []
    return [name for name, rx in _COMPILED.items() if rx.search(text)]


def looks_like_instructions(text: str | None) -> bool:
    signals = instruction_signals(text)
    strong = {"override_instructions", "approval_spoof", "tool_syntax"}
    return len(signals) >= _THRESHOLD or bool(strong.intersection(signals))


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def quotes_untrusted(justification: str, untrusted: str, ngram: int = 5) -> bool:
    """Czy uzasadnienie zawiera dosłowny fragment (n-gram słów) tekstu niezaufanego."""
    src = _words(untrusted)
    if len(src) < ngram:
        return bool(src) and " ".join(src) in " ".join(_words(justification)) and len(src) >= 3
    grams = {" ".join(src[i : i + ngram]) for i in range(len(src) - ngram + 1)}
    j = _words(justification)
    return any(" ".join(j[i : i + ngram]) in grams for i in range(len(j) - ngram + 1))
