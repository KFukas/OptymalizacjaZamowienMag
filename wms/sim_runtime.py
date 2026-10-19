"""Cykl życia silnika symulacji w procesie WMS."""

from __future__ import annotations

import logging
import threading

from fastapi import FastAPI

from wms.deps import WRITE_LOCK
from wms.models import SimState

log = logging.getLogger("wms.sim")
_init_lock = threading.Lock()


def get_engine(app: FastAPI):  # type: ignore[no-untyped-def]
    """Zwraca (i w razie potrzeby tworzy) silnik symulacji; None, jeśli brak danych symulacji."""
    from simulator.engine import SimulationEngine

    with _init_lock:
        engine = getattr(app.state, "sim_engine", None)
        if engine is not None:
            return engine
        with app.state.session_factory() as s:
            if s.get(SimState, 1) is None:
                return None
        engine = SimulationEngine(
            app.state.session_factory, WRITE_LOCK, tick_seconds=app.state.settings.sim_tick_seconds
        )
        app.state.sim_engine = engine
        return engine


def reset(app: FastAPI, *, scenario: str, seed: int, size: str) -> dict:
    from simulator.generate import generate

    with _init_lock:
        old = getattr(app.state, "sim_engine", None)
        if old is not None:
            old.stop_thread()
        app.state.sim_engine = None
    with WRITE_LOCK:
        summary = generate(app.state.engine, seed=seed, size=size, scenario=scenario, preserve_audit=True)
    get_engine(app)
    return {
        "scenario": summary.scenario,
        "seed": summary.seed,
        "size": summary.size,
        "orders": summary.orders,
    }


def shutdown(app: FastAPI) -> None:
    engine = getattr(app.state, "sim_engine", None)
    if engine is not None:
        engine.stop_thread()
