"""Hasła (PBKDF2), JWT, kanoniczny JSON i hashowanie."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

PBKDF2_ITERATIONS = 200_000
JWT_ALG = "HS256"
JWT_ISSUER = "wms-alfa"


def utcnow() -> datetime:
    """Zegar ścienny jako naiwny UTC (spójnie z kolumnami DateTime)."""
    return datetime.now(UTC).replace(tzinfo=None)


def hash_secret(secret: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_secret(secret: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        algo, iters, salt_b64, dk_b64 = encoded.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac("sha256", secret.encode(), base64.b64decode(salt_b64), int(iters))
    return hmac.compare_digest(dk, base64.b64decode(dk_b64))


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def issue_jwt(
    secret: str,
    *,
    subject: str,
    role: str,
    scopes: list[str],
    token_type: str,
    ttl_minutes: int,
    extra: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": JWT_ISSUER,
        "sub": subject,
        "role": role,
        "scopes": sorted(set(scopes)),
        "typ": token_type,
        "iat": int(now.timestamp()),
        "auth_time": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    if extra:
        claims.update(extra)
    return jwt.encode(claims, secret, algorithm=JWT_ALG)


def decode_jwt(secret: str, token: str) -> dict[str, Any]:
    # Jawna lista algorytmów — brak akceptacji "none"/RS↔HS confusion.
    return jwt.decode(
        token,
        secret,
        algorithms=[JWT_ALG],
        issuer=JWT_ISSUER,
        options={"require": ["exp", "iat", "sub", "role", "scopes", "typ"]},
    )
