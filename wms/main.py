"""Aplikacja FastAPI WMS („Magazyn Centralny Alfa”, dane syntetyczne).

Uruchomienie: `uvicorn wms.main:create_app --factory --port 8000`
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine

from wms import __version__
from wms.config import Settings, get_settings
from wms.db import init_db, make_engine, make_sessionmaker
from wms.errors import DomainError, domain_error_handler, request_validation_handler
from wms.routers import approvals, audit, auth, catalog

log = logging.getLogger("wms")


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = engine or make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield

    app = FastAPI(
        title="WMS – Magazyn Centralny Alfa",
        version=__version__,
        description="WMS na danych syntetycznych. Jedyne źródło prawdy; agent ma dostęp wyłącznie przez mcp-wms.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_sessionmaker(engine)
    if engine.dialect.name == "sqlite":
        init_db(engine)  # dev/testy; PostgreSQL: `alembic upgrade head`

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)

    for module in (auth, catalog, approvals, audit):
        app.include_router(module.router)

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    return app
