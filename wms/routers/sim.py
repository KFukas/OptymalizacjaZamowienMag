"""Symulacja (sterowanie: operator/kierownik), KPI, zdarzenia, pozycje pracowników, fale (odczyt)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from wms import audit, ids, sim_runtime
from wms.auth import Principal, require_scope
from wms.deps import WRITE_LOCK, get_session, sim_now
from wms.errors import DomainError, not_found
from wms.models import AgentReport, PickRoute, SimEvent, SimState, Wave, Worker
from wms.schemas import SimControlIn
from wms.services.kpis import compute_kpis
from wms.views import wave_view

router = APIRouter(tags=["simulation"])


def route_view(r: PickRoute) -> dict:
    return {
        "wave_id": r.wave_id,
        "worker_id": r.worker_id,
        "algorithm": r.algorithm,
        "distance_m": round(r.distance_m, 1),
        "baseline_distance_m": round(r.baseline_distance_m, 1),
        "duration_s": round(r.duration_s, 1),
        "trips": r.trips,
        "status": r.status,
        "progress_stop": r.progress_stop,
        "stops": r.stops,
        "path": r.path,
    }


@router.get("/sim/state")
def sim_state(
    request: Request,
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_scope("kpis:read")),
) -> dict:
    state = session.get(SimState, 1)
    if state is None:
        raise not_found("simulation")
    engine = sim_runtime.get_engine(request.app)
    return {
        "sim_now": state.sim_now.isoformat(),
        "running": state.running,
        "speed": state.speed,
        "scenario": state.scenario,
        "seed": state.seed,
        "size": state.size,
        "pack_queue": state.pack_queue,
        "engine": engine.snapshot() if engine else None,
    }


@router.post("/sim/control")
def sim_control(
    body: SimControlIn,
    request: Request,
    principal: Principal = Depends(require_scope("sim:control")),
) -> dict:
    if principal.token_type != "user":
        raise DomainError("FORBIDDEN", "user_token_required", 403)
    engine = sim_runtime.get_engine(request.app)
    if engine is None and body.action != "reset":
        raise not_found("simulation")
    result: dict = {"action": body.action}
    if body.action == "start":
        engine.set_running(True)
    elif body.action == "pause":
        engine.set_running(False)
    elif body.action == "speed":
        if body.speed is None:
            raise DomainError("DOMAIN_VALIDATION", "speed_required", 422)
        engine.set_speed(body.speed)
    elif body.action == "step":
        engine.advance(60.0 * (body.minutes or 1))
    elif body.action == "reset":
        state_seed, state_size = 42, "small"
        with request.app.state.session_factory() as s:
            st = s.get(SimState, 1)
            if st is not None:
                state_seed, state_size = st.seed, st.size
        result.update(
            sim_runtime.reset(
                request.app,
                scenario=body.scenario or "zwykly_dzien",
                seed=body.seed if body.seed is not None else state_seed,
                size=body.size or state_size,
            )
        )
    with WRITE_LOCK, request.app.state.session_factory() as s:
        audit.append(
            s,
            actor=principal.actor_id,
            actor_type="user",
            action=f"sim.{body.action}",
            args=body.model_dump(exclude_none=True),
        )
        s.commit()
    eng = sim_runtime.get_engine(request.app)
    result["state"] = eng.snapshot() if eng else None
    return result


@router.get("/kpis")
def kpis(
    window: Annotated[Literal["15m", "1h", "shift", "day"], Query()] = "1h",
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_scope("kpis:read")),
) -> dict:
    return compute_kpis(session, window)


@router.get("/sim/events")
def events(
    kind: Annotated[
        Literal["pick", "stockout", "shipped", "wave_completed", "order_arrived"] | None, Query()
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_scope("kpis:read")),
) -> dict:
    q = select(SimEvent).order_by(SimEvent.id.desc()).limit(limit)
    if kind:
        q = q.where(SimEvent.kind == kind)
    return {
        "events": [
            {
                "event_ref": e.ref,
                "ts": e.ts_sim.isoformat(),
                "kind": e.kind,
                "worker_id": e.worker_id,
                "wave_id": e.wave_id,
                "order_id": e.order_id,
                "location_id": e.location_id,
                "sku_id": e.sku_id,
                "details": e.details,
            }
            for e in session.execute(q).scalars()
        ]
    }


@router.get("/sim/workers")
def worker_positions(
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_scope("workers:read")),
) -> dict:
    ws = session.execute(select(Worker).order_by(Worker.id)).scalars()
    return {
        "sim_now": sim_now(session).isoformat(),
        "workers": [
            {
                "worker_id": w.id,
                "x": round(w.pos_x, 2),
                "y": round(w.pos_y, 2),
                "state": w.state,
                "active": w.active,
            }
            for w in ws
        ],
    }


@router.get("/waves")
def list_waves(
    status: Annotated[
        Literal["validated", "released", "in_progress", "completed", "discarded"] | None, Query()
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_scope("waves:read")),
) -> dict:
    q = select(Wave).order_by(Wave.created_wall.desc()).limit(limit)
    if status:
        q = q.where(Wave.status == status)
    return {
        "sim_now": sim_now(session).isoformat(),
        "waves": [wave_view(w) for w in session.execute(q).scalars()],
    }


@router.get("/waves/{wave_id}")
def get_wave(
    wave_id: Annotated[str, Path(pattern=ids.WAVE_ID)],
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_scope("waves:read")),
) -> dict:
    wave = session.get(Wave, wave_id)
    if wave is None:
        raise not_found("wave")
    routes = session.execute(select(PickRoute).where(PickRoute.wave_id == wave_id)).scalars()
    return {**wave_view(wave), "routes": [route_view(r) for r in routes]}


@router.get("/routes/active")
def active_routes(
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_scope("waves:read")),
) -> dict:
    rows = session.execute(
        select(PickRoute)
        .where(PickRoute.status.in_(["planned", "active"]))
        .order_by(PickRoute.id.desc())
        .limit(60)
    ).scalars()
    return {"routes": [route_view(r) for r in rows]}


@router.get("/reports")
def reports(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_scope("reports:read")),
) -> dict:
    rows = session.execute(select(AgentReport).order_by(AgentReport.id.desc()).limit(limit)).scalars()
    return {
        "reports": [
            {
                "id": r.id,
                "created_at": r.created_wall.isoformat(),
                "sim_time": r.sim_time.isoformat(),
                "author": r.author,
                "mode": r.mode,
                "summary": r.summary,
                "decisions": r.decisions,
                "metrics": r.metrics,
            }
            for r in rows
        ]
    }
