"""Silnik bazy, sesje i zabezpieczenia na poziomie bazy (append-only audytu)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


# Wyzwalacze blokujące UPDATE/DELETE w tabeli audytu. Aplikacja nie ma ścieżki kodu modyfikującej
# wpisy, a te wyzwalacze dodatkowo chronią przed przypadkowym/umyślnym UPDATE z poziomu SQL.
# Łańcuch hashy (wms/audit.py) wykrywa modyfikacje wykonane z pominięciem wyzwalaczy.
SQLITE_GUARDS = [
    """
    CREATE TRIGGER IF NOT EXISTS audit_log_no_update
    BEFORE UPDATE ON audit_log
    BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
    BEFORE DELETE ON audit_log
    BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
    """,
]

POSTGRES_GUARDS = [
    """
    CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
    BEGIN
      RAISE EXCEPTION 'audit_log is append-only';
    END;
    $$ LANGUAGE plpgsql;
    """,
    "DROP TRIGGER IF EXISTS audit_log_no_modify ON audit_log;",
    """
    CREATE TRIGGER audit_log_no_modify
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
    """,
]


def guard_statements(dialect: str) -> list[str]:
    if dialect == "sqlite":
        return SQLITE_GUARDS
    if dialect.startswith("postgres"):
        return POSTGRES_GUARDS
    return []


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        if url.startswith("sqlite:///") and ":memory:" not in url:
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict = {"connect_args": {"check_same_thread": False, "timeout": 30}}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _rec):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if ":memory:" not in url:
                cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return engine
    return create_engine(url, pool_pre_ping=True)


def init_db(engine: Engine) -> None:
    """Tworzy schemat (dev/testy) + wyzwalacze. W produkcji używaj `alembic upgrade head`."""
    from wms import models  # noqa: F401  (rejestracja modeli)

    Base.metadata.create_all(engine)
    install_guards(engine)


def install_guards(engine: Engine) -> None:
    with engine.begin() as conn:
        for stmt in guard_statements(engine.dialect.name):
            conn.execute(text(stmt))


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
    finally:
        session.close()
