"""Silnik polityki: tożsamość -> rola -> allowlista narzędzi -> walidacja schematu -> limity.

Każda decyzja jest podejmowana na podstawie NAZWY narzędzia, tożsamości klienta i argumentów —
nigdy na podstawie opisu narzędzia ani treści wysłanej przez model.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from mcp_wms.policy.loader import Policy


@dataclass(frozen=True)
class Principal:
    id: str
    role: str
    allowed_tools: frozenset[str]


@dataclass(frozen=True)
class Decision:
    allowed: bool
    error: str = ""
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def body(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error": self.error, "reason": self.reason}
        if self.details:
            out.update(self.details)
        return out


ALLOW = Decision(True)


def deny(reason: str, **details: Any) -> Decision:
    return Decision(False, "POLICY_DENIED", reason, details)


class SlidingWindowLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: tuple[str, str], calls: int, per_seconds: int) -> bool:
        now = self._clock()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - per_seconds:
                q.popleft()
            if len(q) >= calls:
                return False
            q.append(now)
            return True


class PolicyEngine:
    def __init__(
        self,
        policy: Policy,
        environ: dict[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.policy = policy
        self._tokens = policy.token_index(environ)
        self.limiter = SlidingWindowLimiter(clock)

    # ------------------------------------------------------------------ tożsamość
    def authenticate(self, token: str | None) -> Principal | None:
        if not token:
            return None
        presented = hashlib.sha256(token.encode()).hexdigest()
        match = None
        for digest, cfg in self._tokens.items():  # porównanie w czasie stałym
            if hmac.compare_digest(digest, presented):
                match = cfg
        if match is None:
            return None
        role = self.policy.roles[match.role]
        return Principal(match.id, match.role, frozenset(role.allow_tools))

    # ------------------------------------------------------------------ autoryzacja
    def authorize(self, principal: Principal | None, tool: str, registered: set[str]) -> Decision:
        if principal is None:
            return deny("unauthenticated")
        # Nieznane i niedozwolone narzędzie dają tę samą odpowiedź (brak enumeracji ukrytych narzędzi).
        if tool not in registered or tool not in principal.allowed_tools:
            return deny("tool_not_permitted")
        return ALLOW

    def check_size(self, arguments: Any) -> Decision:
        try:
            raw = json.dumps(arguments, ensure_ascii=False)
        except (TypeError, ValueError):
            return Decision(False, "SCHEMA_VALIDATION", "invalid_arguments")
        if len(raw.encode()) > self.policy.limits.max_argument_bytes:
            return deny("arguments_too_large")
        return ALLOW

    @staticmethod
    def validate(model: type[BaseModel], arguments: Any) -> tuple[BaseModel | None, Decision]:
        if not isinstance(arguments, dict):
            return None, Decision(False, "SCHEMA_VALIDATION", "invalid_arguments")
        try:
            # Tryb JSON + strict: identyczna semantyka jak dla danych z sieci, bez koercji typów.
            return model.model_validate_json(json.dumps(arguments)), ALLOW
        except ValidationError as exc:
            fields = [
                {"loc": [str(p) for p in e.get("loc", ())][-3:], "type": e.get("type", "invalid")}
                for e in exc.errors(include_input=False, include_url=False)[:10]
            ]
            return None, Decision(False, "SCHEMA_VALIDATION", "invalid_arguments", {"fields": fields})

    def rate(self, principal: Principal, tool: str) -> Decision:
        cfg = self.policy.rate_for(tool)
        if not self.limiter.hit((principal.id, tool), cfg.calls, cfg.per_seconds):
            return deny("rate_limited", retry_after_s=cfg.per_seconds)
        return ALLOW
