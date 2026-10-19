"""Uwierzytelnianie i autoryzacja WMS: role, scope'y, zależności FastAPI.

Obrona w głąb: KAŻDY endpoint deklaruje wymagany scope. Token usługowy mcp-wms dostaje wyłącznie
scope'y z `SERVICE_CLIENTS["mcp-wms"]` — nie ma wśród nich `approvals:decide`, `approvals:read_all`,
`audit:read`, `slotting:decide` ani `sim:control`, więc nawet przejęty mcp-wms nie wykona decyzji kierownika.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import jwt
from fastapi import Depends, Header, HTTPException, Request, status

from wms.config import Settings
from wms.security import decode_jwt


class Role(StrEnum):
    OPERATOR = "operator"
    MANAGER = "kierownik_zmiany"
    AGENT_SERVICE = "agent_service"


READ_SCOPES = [
    "orders:read",
    "inventory:read",
    "layout:read",
    "workers:read",
    "kpis:read",
    "waves:read",
    "slotting:read",
    "forecast:read",
]

ROLE_SCOPES: dict[Role, list[str]] = {
    Role.OPERATOR: [*READ_SCOPES, "approvals:request", "approvals:read_own", "sim:control", "reports:read"],
    Role.MANAGER: [
        *READ_SCOPES,
        "approvals:request",
        "approvals:read_own",
        "approvals:read_all",
        "approvals:decide",
        "slotting:decide",
        "audit:read",
        "sim:control",
        "reports:read",
    ],
    Role.AGENT_SERVICE: [
        *READ_SCOPES,
        "waves:propose",
        "waves:release",
        "routes:propose",
        "slotting:propose",
        "approvals:request",
        "approvals:read_own",
        "audit:append",
        "reports:write",
    ],
}

# Scope'y, których nie może mieć żaden token usługowy — sprawdzane przy wydawaniu tokenu i w testach.
MANAGER_ONLY_SCOPES = frozenset(
    {"approvals:decide", "approvals:read_all", "audit:read", "slotting:decide", "sim:control"}
)


@dataclass(frozen=True)
class ServiceClient:
    client_id: str
    role: Role
    secret_setting: str  # nazwa pola w Settings z sekretem


SERVICE_CLIENTS: dict[str, ServiceClient] = {
    "mcp-wms": ServiceClient("mcp-wms", Role.AGENT_SERVICE, "wms_mcp_client_secret"),
}


@dataclass(frozen=True)
class Principal:
    subject: str  # np. "kierownik1" albo "service:mcp-wms"
    role: Role
    scopes: frozenset[str]
    token_type: str  # user | service
    acting_for: str | None = None  # atrybucja agenta (nagłówek od mcp-wms), NIE służy do autoryzacji
    claims: dict = field(default_factory=dict, compare=False)

    @property
    def actor_id(self) -> str:
        """Identyfikator do audytu i separation of duties."""
        if self.token_type == "service" and self.acting_for:
            return f"{self.subject}/agent:{self.acting_for}"
        return self.subject

    def has(self, scope: str) -> bool:
        return scope in self.scopes


def _error(code: int, error: str, reason: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"error": error, "reason": reason})


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_acting_principal: str | None = Header(default=None, alias="X-Acting-Principal"),
) -> Principal:
    settings: Settings = request.app.state.settings
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _error(status.HTTP_401_UNAUTHORIZED, "UNAUTHENTICATED", "missing_bearer_token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_jwt(settings.effective_jwt_secret(), token)
    except jwt.ExpiredSignatureError as exc:
        raise _error(status.HTTP_401_UNAUTHORIZED, "UNAUTHENTICATED", "token_expired") from exc
    except jwt.PyJWTError as exc:
        raise _error(status.HTTP_401_UNAUTHORIZED, "UNAUTHENTICATED", "invalid_token") from exc
    try:
        role = Role(claims["role"])
    except ValueError as exc:
        raise _error(status.HTTP_401_UNAUTHORIZED, "UNAUTHENTICATED", "invalid_role") from exc
    token_type = str(claims["typ"])
    # Scope'y z tokenu są przecinane z dozwolonymi dla roli — nawet poprawnie podpisany token z
    # nadmiarowymi scope'ami nie da więcej, niż przewiduje rola.
    scopes = frozenset(claims.get("scopes", [])) & frozenset(ROLE_SCOPES[role])
    if token_type == "service":
        scopes = scopes - MANAGER_ONLY_SCOPES
        if role is not Role.AGENT_SERVICE:
            raise _error(status.HTTP_401_UNAUTHORIZED, "UNAUTHENTICATED", "invalid_role")
    elif role is Role.AGENT_SERVICE:
        raise _error(status.HTTP_401_UNAUTHORIZED, "UNAUTHENTICATED", "invalid_role")
    acting = None
    if token_type == "service" and x_acting_principal:
        acting = "".join(ch for ch in x_acting_principal[:48] if ch.isalnum() or ch in "-_.")
    return Principal(
        subject=str(claims["sub"]),
        role=role,
        scopes=scopes,
        token_type=token_type,
        acting_for=acting or None,
        claims=claims,
    )


def require_scope(scope: str):  # type: ignore[no-untyped-def]
    def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has(scope):
            raise _error(status.HTTP_403_FORBIDDEN, "FORBIDDEN", f"missing_scope:{scope}")
        return principal

    return _dep


def require_user_role(role: Role, scope: str):  # type: ignore[no-untyped-def]
    """Endpointy UI kierownika: token użytkownika (nie usługowy) o konkretnej roli + scope."""

    def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.token_type != "user" or principal.role is not role or not principal.has(scope):
            raise _error(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "manager_role_required")
        return principal

    return _dep
