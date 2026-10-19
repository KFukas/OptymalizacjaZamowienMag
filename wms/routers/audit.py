"""Dziennik audytu: odczyt i weryfikacja (kierownik), dopisywanie przez mcp-wms (scope audit:append)."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from wms import audit
from wms.auth import Principal, Role, require_scope, require_user_role
from wms.deps import get_session, get_write_session
from wms.models import AuditLog
from wms.schemas import AuditAppendIn

router = APIRouter(tags=["audit"])


def _row(r: AuditLog) -> dict:
    try:
        args = json.loads(r.args)
    except ValueError:
        args = {"_raw": r.args}
    return {
        "seq": r.seq,
        "ts": r.ts.isoformat(),
        "source": r.source,
        "actor": r.actor,
        "actor_type": r.actor_type,
        "action": r.action,
        "target": r.target,
        "args": args,
        "decision": r.decision,
        "outcome": r.outcome,
        "reason": r.reason,
        "prev_hash": r.prev_hash,
        "hash": r.hash,
    }


@router.get("/audit")
def list_audit(
    actor: Annotated[str | None, Query(max_length=96)] = None,
    action: Annotated[str | None, Query(max_length=64)] = None,
    decision: Annotated[Literal["allow", "deny"] | None, Query()] = None,
    source: Annotated[Literal["wms", "mcp"] | None, Query()] = None,
    before_seq: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_user_role(Role.MANAGER, "audit:read")),
) -> dict:
    q = select(AuditLog).order_by(AuditLog.seq.desc()).limit(limit)
    if actor:
        q = q.where(AuditLog.actor.contains(actor))
    if action:
        q = q.where(AuditLog.action.startswith(action))
    if decision:
        q = q.where(AuditLog.decision == decision)
    if source:
        q = q.where(AuditLog.source == source)
    if before_seq:
        q = q.where(AuditLog.seq < before_seq)
    return {"entries": [_row(r) for r in session.execute(q).scalars()]}


@router.get("/audit/verify")
def verify(
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_user_role(Role.MANAGER, "audit:read")),
) -> dict:
    return audit.verify_chain(session).as_dict()


@router.post("/internal/audit", status_code=201, tags=["internal"])
def append_from_mcp(
    body: AuditAppendIn,
    session: Session = Depends(get_write_session),
    principal: Principal = Depends(require_scope("audit:append")),
) -> dict:
    # Aktor jest prefiksowany tożsamością usługi z tokenu — mcp-wms nie może podszyć się pod kierownika.
    actor = f"{principal.subject}/{body.actor}"
    row = audit.append(
        session,
        source="mcp",
        actor=actor,
        actor_type="agent",
        action=f"mcp.{body.action}",
        target=body.target,
        args=body.args,
        decision=body.decision,
        outcome=body.outcome,
        reason=body.reason,
    )
    session.commit()
    return {"seq": row.seq, "hash": row.hash}
