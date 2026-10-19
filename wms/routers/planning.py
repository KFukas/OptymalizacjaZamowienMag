"""Planowanie: propozycje fal, trasy, zwolnienie, prognoza, slotting, sugestie, heatmapa, raporty agenta."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from wms import ids
from wms.auth import Principal, Role, require_scope, require_user_role
from wms.config import Settings
from wms.deps import get_session, get_write_session, sim_now
from wms.models import AgentReport, ReslottingProposal
from wms.schemas import (
    AgentReportIn,
    ForecastIn,
    ReslottingDecisionIn,
    ReslottingProposalIn,
    RouteComputeIn,
    WaveProposalIn,
    WaveReleaseIn,
)
from wms.security import utcnow
from wms.services import idempotency, planning, waves

router = APIRouter(tags=["planning"])
WaveIdPath = Annotated[str, Path(pattern=ids.WAVE_ID)]


def _settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


@router.post("/waves", status_code=201)
def propose_wave(
    body: WaveProposalIn,
    request: Request,
    session: Session = Depends(get_write_session),
    principal: Principal = Depends(require_scope("waves:propose")),
) -> dict:
    payload = body.model_dump(mode="json")
    replay = idempotency.lookup(session, principal.actor_id, body.idempotency_key, "wave.propose", payload)
    if replay:
        return replay
    result = waves.propose_wave(
        session,
        _settings(request),
        principal,
        order_ids=body.order_ids,
        worker_ids=body.worker_ids,
        cutoff=body.cutoff_time,
        rationale=body.rationale,
        dry_run=body.dry_run,
    )
    if not body.dry_run:
        idempotency.store(
            session, principal.actor_id, body.idempotency_key, "wave.propose", payload, result, 201
        )
        session.commit()
    return result


@router.post("/waves/{wave_id}/routes")
def compute_route(
    wave_id: WaveIdPath,
    body: RouteComputeIn,
    request: Request,
    session: Session = Depends(get_write_session),
    principal: Principal = Depends(require_scope("routes:propose")),
) -> dict:
    result = waves.compute_route(
        session,
        _settings(request),
        principal,
        wave_id=wave_id,
        worker_id=body.worker_id,
        algorithm=body.algorithm,
    )
    session.commit()
    return result


@router.post("/waves/{wave_id}/release")
def release_wave(
    wave_id: WaveIdPath,
    body: WaveReleaseIn,
    request: Request,
    session: Session = Depends(get_write_session),
    principal: Principal = Depends(require_scope("waves:release")),
) -> dict:
    payload = {"wave_id": wave_id, **body.model_dump(mode="json")}
    replay = idempotency.lookup(session, principal.actor_id, body.idempotency_key, "wave.release", payload)
    if replay:
        return replay
    result = waves.release_wave(session, _settings(request), principal, wave_id=wave_id)
    idempotency.store(session, principal.actor_id, body.idempotency_key, "wave.release", payload, result)
    session.commit()
    return result


@router.post("/waves/{wave_id}/discard")
def discard_wave(
    wave_id: WaveIdPath,
    session: Session = Depends(get_write_session),
    principal: Principal = Depends(require_scope("waves:propose")),
) -> dict:
    result = waves.discard_wave(session, principal, wave_id=wave_id)
    session.commit()
    return result


@router.get("/planning/wave-suggestions")
def wave_suggestions(
    max_waves: Annotated[int, Query(ge=1, le=10)] = 3,
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_scope("waves:read")),
) -> dict:
    return planning.wave_suggestions(session, max_waves)


@router.post("/forecast")
def forecast(
    body: ForecastIn,
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_scope("forecast:read")),
) -> dict:
    return planning.forecast(session, body.sku_ids, body.horizon_days)


@router.get("/slotting/analysis")
def slotting_analysis(
    max_moves: Annotated[int, Query(ge=1, le=50)] = 20,
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_scope("slotting:read")),
) -> dict:
    return planning.slotting_analysis(session, max_moves)


@router.post("/slotting/proposals", status_code=201)
def propose_reslotting(
    body: ReslottingProposalIn,
    session: Session = Depends(get_write_session),
    principal: Principal = Depends(require_scope("slotting:propose")),
) -> dict:
    payload = body.model_dump(mode="json")
    replay = idempotency.lookup(
        session, principal.actor_id, body.idempotency_key, "slotting.propose", payload
    )
    if replay:
        return replay
    result = planning.propose_reslotting(
        session,
        principal,
        moves=[m.model_dump() for m in body.moves],
        rationale=body.rationale,
        dry_run=body.dry_run,
    )
    if not body.dry_run:
        idempotency.store(
            session, principal.actor_id, body.idempotency_key, "slotting.propose", payload, result, 201
        )
        session.commit()
    return result


@router.get("/slotting/proposals")
def list_reslotting(
    status: Annotated[Literal["proposed", "applied", "rejected", "failed"] | None, Query()] = None,
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_scope("slotting:read")),
) -> dict:
    q = select(ReslottingProposal).order_by(ReslottingProposal.created_wall.desc()).limit(100)
    if status:
        q = q.where(ReslottingProposal.status == status)
    return {"proposals": [planning.reslotting_view(p) for p in session.execute(q).scalars()]}


@router.post("/slotting/proposals/{proposal_id}/decide")
def decide_reslotting(
    proposal_id: Annotated[str, Path(pattern=ids.RESLOTTING_ID)],
    body: ReslottingDecisionIn,
    session: Session = Depends(get_write_session),
    principal: Principal = Depends(require_user_role(Role.MANAGER, "slotting:decide")),
) -> dict:
    result = planning.decide_reslotting(
        session,
        principal,
        proposal_id=proposal_id,
        decision=body.decision,
        comment=body.comment,
        expected_version=body.expected_version,
    )
    session.commit()
    return result


@router.get("/layout/heatmap")
def heatmap(
    metric: Annotated[Literal["rotation", "fill"], Query()] = "rotation",
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_scope("layout:read")),
) -> dict:
    return planning.heatmap(session, metric)


@router.post("/reports", status_code=201)
def submit_report(
    body: AgentReportIn,
    session: Session = Depends(get_write_session),
    principal: Principal = Depends(require_scope("reports:write")),
) -> dict:
    report = AgentReport(
        created_wall=utcnow(),
        sim_time=sim_now(session),
        author=principal.actor_id,
        mode=body.mode,
        summary=body.summary,
        decisions=body.decisions,
        metrics=body.metrics,
    )
    session.add(report)
    session.commit()
    return {"report_id": report.id}
