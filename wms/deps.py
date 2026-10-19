"""Zależności FastAPI: sesje DB, blokada zapisu, zegar symulacji."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import datetime

from fastapi import Request
from sqlalchemy.orm import Session

from wms.models import SimState

# Jedna blokada zapisu na proces WMS: serializuje transakcje zapisujące (API + tick symulatora).
# SQLite i tak ma jednego pisarza; w PostgreSQL dodatkowo działa blokada wiersza audit_head.
# UWAGA: zwykły Lock, nie RLock — FastAPI wykonuje wejście i wyjście zależności-generatora w RÓŻNYCH wątkach
# puli, a RLock może zwolnić tylko wątek, który go przejął (osierocona blokada = zakleszczenie zapisów).
# Blokada nie jest reentrantna: kod wykonywany pod nią nie może jej brać ponownie.
WRITE_LOCK = threading.Lock()

DEFAULT_SIM_START = datetime(2026, 11, 2, 6, 0, 0)


def get_session(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_write_session(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.session_factory()
    with WRITE_LOCK:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def sim_now(session: Session) -> datetime:
    state = session.get(SimState, 1)
    return state.sim_now if state else DEFAULT_SIM_START
