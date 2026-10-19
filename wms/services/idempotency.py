"""Idempotencja operacji zapisu: (principal, idempotency_key) -> zapisana odpowiedź."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from wms.errors import DomainError
from wms.models import IdempotencyRecord
from wms.security import canonical_json, sha256_hex, utcnow


def request_fingerprint(endpoint: str, payload: dict[str, Any]) -> str:
    body = {k: v for k, v in payload.items() if k != "idempotency_key"}
    return sha256_hex(canonical_json({"endpoint": endpoint, "body": body}))


def lookup(
    session: Session, principal: str, key: str | None, endpoint: str, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """Zwraca zapisaną odpowiedź dla powtórzonego żądania albo None. Ten sam klucz z innym
    ładunkiem = konflikt (ochrona przed „przemyceniem” innej operacji pod znanym kluczem)."""
    if not key:
        return None
    rec = session.get(IdempotencyRecord, (principal, key))
    if rec is None:
        return None
    if rec.endpoint != endpoint or rec.request_hash != request_fingerprint(endpoint, payload):
        raise DomainError("IDEMPOTENCY_CONFLICT", "key_reused_with_different_payload", 409)
    return {**rec.response, "idempotent_replay": True}


def store(
    session: Session,
    principal: str,
    key: str | None,
    endpoint: str,
    payload: dict[str, Any],
    response: dict[str, Any],
    status_code: int = 200,
) -> None:
    if not key:
        return
    session.add(
        IdempotencyRecord(
            principal=principal,
            key=key,
            endpoint=endpoint,
            request_hash=request_fingerprint(endpoint, payload),
            status_code=status_code,
            response=response,
            created_wall=utcnow(),
        )
    )
