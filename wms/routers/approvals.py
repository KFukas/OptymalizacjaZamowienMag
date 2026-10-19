"""Wnioski o operacje nieodwracalne i decyzje kierownika zmiany.

- `POST /approvals/order-cancellation`, `POST /approvals/inventory-adjustment` — scope `approvals:request`
  (agent przez mcp-wms, operator/kierownik z UI). Tworzą wyłącznie wniosek `pending`.
- `GET /approvals/mine/{id}` — scope `approvals:read_own`, tylko własne wnioski.
- `GET /approvals`, `GET /approvals/{id}`, `POST /approvals/{id}/decide` — wyłącznie token UŻYTKOWNIKA
  roli `kierownik_zmiany` (token usługowy -> 403), decyzja dodatkowo z PIN-em.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from wms import ids
from wms.auth import Principal, Role, require_scope, require_user_role
from wms.config import Settings
from wms.deps import get_session, get_write_session
from wms.errors import not_found
from wms.models import ApprovalRequest, Order, StockItem
from wms.schemas import AdjustmentRequestIn, CancellationRequestIn, DecisionIn
from wms.services import approvals as svc
from wms.services import idempotency
from wms.views import order_view, stock_view

router = APIRouter(prefix="/approvals", tags=["approvals"])

ApprovalIdPath = Annotated[str, Path(pattern=ids.APPROVAL_ID)]


def _settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


@router.post("/order-cancellation", status_code=201)
def request_cancellation(
    body: CancellationRequestIn,
    request: Request,
    session: Session = Depends(get_write_session),
    principal: Principal = Depends(require_scope("approvals:request")),
) -> dict:
    payload = body.model_dump(mode="json")
    replay = idempotency.lookup(session, principal.actor_id, body.idempotency_key, "approval.cancel", payload)
    if replay:
        return replay
    req = svc.request_order_cancellation(
        session,
        _settings(request),
        principal,
        order_id=body.order_id,
        reason_code=body.reason_code,
        justification=body.justification,
    )
    resp = svc.to_public(req, include_context=False)
    idempotency.store(
        session, principal.actor_id, body.idempotency_key, "approval.cancel", payload, resp, 201
    )
    session.commit()
    return resp


@router.post("/inventory-adjustment", status_code=201)
def request_adjustment(
    body: AdjustmentRequestIn,
    request: Request,
    session: Session = Depends(get_write_session),
    principal: Principal = Depends(require_scope("approvals:request")),
) -> dict:
    payload = body.model_dump(mode="json")
    replay = idempotency.lookup(session, principal.actor_id, body.idempotency_key, "approval.adjust", payload)
    if replay:
        return replay
    req = svc.request_inventory_adjustment(
        session,
        _settings(request),
        principal,
        location_id=body.location_id,
        sku_id=body.sku_id,
        counted_qty=body.counted_qty,
        reason_code=body.reason_code,
        justification=body.justification,
        evidence_ref=body.evidence_ref,
    )
    resp = svc.to_public(req, include_context=False)
    idempotency.store(
        session, principal.actor_id, body.idempotency_key, "approval.adjust", payload, resp, 201
    )
    session.commit()
    return resp


@router.get("/mine/{request_id}")
def get_own_request(
    request_id: ApprovalIdPath,
    session: Session = Depends(get_write_session),
    principal: Principal = Depends(require_scope("approvals:read_own")),
) -> dict:
    req = session.get(ApprovalRequest, request_id)
    # Cudzy i nieistniejący wniosek dają identyczną odpowiedź (brak enumeracji).
    if req is None or req.requested_by != principal.actor_id:
        raise not_found("approval_request")
    if svc.expire_if_due(session, req):
        session.commit()
    return svc.to_public(req, include_context=False)


@router.get("")
def list_requests(
    status: Annotated[
        Literal["pending", "approved", "executed", "rejected", "expired", "failed"] | None, Query()
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    session: Session = Depends(get_write_session),
    _p: Principal = Depends(require_user_role(Role.MANAGER, "approvals:read_all")),
) -> dict:
    if svc.expire_all_due(session):
        session.commit()
    q = select(ApprovalRequest).order_by(ApprovalRequest.created_wall.desc()).limit(limit)
    if status:
        q = q.where(ApprovalRequest.status == status)
    reqs = session.execute(q).scalars().all()
    return {"requests": [svc.to_public(r, include_context=True) for r in reqs]}


@router.get("/{request_id}")
def get_request(
    request_id: ApprovalIdPath,
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_user_role(Role.MANAGER, "approvals:read_all")),
) -> dict:
    req = session.get(ApprovalRequest, request_id)
    if req is None:
        raise not_found("approval_request")
    data = svc.to_public(req, include_context=True)
    # Aktualny stan celu (do porównania z before_state — kierownik widzi, czy stan się zmienił).
    if req.target_type == "order":
        order = session.get(Order, req.target_id)
        data["current_target"] = order_view(order) if order else None
        data["target_changed"] = bool(order and order.version != req.target_version)
    else:
        stock = session.get(StockItem, int(req.target_id))
        data["current_target"] = stock_view(stock) if stock else None
        data["target_changed"] = bool(stock and stock.version != req.target_version)
    return data


@router.post("/{request_id}/decide")
def decide(
    request_id: ApprovalIdPath,
    body: DecisionIn,
    request: Request,
    session: Session = Depends(get_write_session),
    principal: Principal = Depends(require_user_role(Role.MANAGER, "approvals:decide")),
) -> dict:
    req = svc.decide(
        session,
        _settings(request),
        principal,
        request_id=request_id,
        decision=body.decision,
        comment=body.comment,
        pin=body.pin,
        expected_version=body.expected_version,
    )
    return svc.to_public(req, include_context=True)
