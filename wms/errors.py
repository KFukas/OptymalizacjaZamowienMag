"""Ustrukturyzowane błędy domenowe. Komunikaty nie ujawniają wnętrza systemu."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class DomainError(Exception):
    def __init__(
        self,
        error: str,
        reason: str,
        status_code: int = 422,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{error}:{reason}")
        self.error = error
        self.reason = reason
        self.status_code = status_code
        self.details = details or {}

    def body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.error, "reason": self.reason}
        if self.details:
            body["details"] = self.details
        return body


def not_found(what: str) -> DomainError:
    return DomainError("NOT_FOUND", f"{what}_not_found", 404)


def invalid_state(reason: str, details: dict[str, Any] | None = None) -> DomainError:
    return DomainError("INVALID_STATE", reason, 409, details)


def validation(reason: str, details: dict[str, Any] | None = None) -> DomainError:
    return DomainError("DOMAIN_VALIDATION", reason, 422, details)


def rate_limited(reason: str) -> DomainError:
    return DomainError("RATE_LIMITED", reason, 429)


async def domain_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    return JSONResponse(status_code=exc.status_code, content=exc.body())


async def request_validation_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Zwracamy tylko lokalizację i typ błędu — bez echa wartości wejściowych.
    fields = [
        {"loc": [str(p) for p in err.get("loc", [])][-3:], "type": err.get("type", "invalid")}
        for err in exc.errors()[:20]
    ]
    return JSONResponse(
        status_code=422,
        content={"error": "SCHEMA_VALIDATION", "reason": "invalid_arguments", "fields": fields},
    )
