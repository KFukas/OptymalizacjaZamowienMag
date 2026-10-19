from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from wms import audit
from wms.auth import ROLE_SCOPES, SERVICE_CLIENTS, Principal, Role, get_principal
from wms.config import Settings
from wms.deps import WRITE_LOCK, get_session
from wms.errors import DomainError
from wms.models import User
from wms.schemas import ClientCredentialsIn, LoginIn
from wms.security import issue_jwt, verify_secret

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def login(body: LoginIn, request: Request, session: Session = Depends(get_session)) -> dict:
    settings: Settings = request.app.state.settings
    user = session.get(User, body.username)
    ok = user is not None and user.active and verify_secret(body.password, user.password_hash)
    with WRITE_LOCK:
        audit.append(
            session,
            actor=body.username,
            actor_type="user",
            action="auth.login",
            decision="allow" if ok else "deny",
            outcome="ok" if ok else "denied",
            reason="" if ok else "invalid_credentials",
        )
        session.commit()
    if not ok or user is None:
        raise DomainError("UNAUTHENTICATED", "invalid_credentials", 401)
    role = Role(user.role)
    if role is Role.AGENT_SERVICE:
        raise DomainError("UNAUTHENTICATED", "invalid_credentials", 401)
    token = issue_jwt(
        settings.effective_jwt_secret(),
        subject=user.username,
        role=role.value,
        scopes=ROLE_SCOPES[role],
        token_type="user",  # noqa: S106
        ttl_minutes=settings.jwt_ttl_minutes,
        extra={"name": user.display_name},
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": role.value,
        "display_name": user.display_name,
        "expires_in": settings.jwt_ttl_minutes * 60,
    }


@router.post("/token")
def service_token(body: ClientCredentialsIn, request: Request) -> dict:
    """client_credentials dla usług. Scope'y wynikają z konfiguracji klienta w WMS — żądanie ich nie wybiera."""
    settings: Settings = request.app.state.settings
    client = SERVICE_CLIENTS.get(body.client_id)
    expected = getattr(settings, client.secret_setting, "") if client else ""
    if client is None or not expected or not hmac.compare_digest(body.client_secret, expected):
        raise DomainError("UNAUTHENTICATED", "invalid_client", 401)
    token = issue_jwt(
        settings.effective_jwt_secret(),
        subject=f"service:{client.client_id}",
        role=client.role.value,
        scopes=ROLE_SCOPES[client.role],
        token_type="service",  # noqa: S106
        ttl_minutes=settings.service_token_ttl_minutes,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "scopes": sorted(ROLE_SCOPES[client.role]),
        "expires_in": settings.service_token_ttl_minutes * 60,
    }


@router.get("/me")
def me(principal: Principal = Depends(get_principal)) -> dict:
    return {
        "subject": principal.subject,
        "actor_id": principal.actor_id,
        "role": principal.role.value,
        "token_type": principal.token_type,
        "scopes": sorted(principal.scopes),
        "display_name": principal.claims.get("name", principal.subject),
    }
