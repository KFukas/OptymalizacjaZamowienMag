"""Cykl życia ApprovalRequest: złożenie wniosku -> decyzja kierownika -> wykonanie w jednej transakcji.

Gwarancje (egzekwowane tutaj, w WMS — niezależnie od mcp-wms):
- żaden parametr wniosku nie ustawia statusu; wniosek zawsze powstaje jako `pending`,
- decyzję podejmuje wyłącznie użytkownik roli `kierownik_zmiany` z ponownym potwierdzeniem PIN-em,
- separation of duties: decydujący != wnioskujący,
- TTL: po `expires_wall` wniosek przechodzi w `expired` i nie może być zatwierdzony,
- replay: decyzja wymaga `expected_version` i statusu `pending`,
- optimistic locking: wykonanie sprawdza, czy wersja celu (zamówienia / pozycji zapasu) nie zmieniła
  się od złożenia wniosku; jeśli tak -> `failed` (stale_state), bez zmiany celu.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from wms import audit
from wms.auth import Principal, Role
from wms.config import Settings
from wms.errors import DomainError, invalid_state, not_found, rate_limited, validation
from wms.ids import next_id
from wms.models import (
    ApprovalRequest,
    Order,
    PickRoute,
    SimEvent,
    StockItem,
    User,
    Wave,
)
from wms.security import utcnow, verify_secret
from wms.states import (
    APPROVAL_TRANSITIONS,
    CANCELLABLE_ORDER_STATES,
    ORDER_TRANSITIONS,
    ApprovalStatus,
    OrderStatus,
    WaveStatus,
    transition,
)
from wms.untrusted import instruction_signals, looks_like_instructions, quotes_untrusted

CANCEL_REASONS = {
    "customer_request",
    "suspected_fraud",
    "duplicate_order",
    "address_invalid",
    "unfulfillable_stock",
}
ADJUST_REASONS = {"cycle_count", "damage", "stockout_event", "found_stock", "system_error"}
EVIDENCE_EVENT_KINDS = {"stockout", "count_discrepancy"}
LIVE_STATUSES = [ApprovalStatus.PENDING.value, ApprovalStatus.APPROVED.value, ApprovalStatus.EXECUTED.value]


def _actor_type(p: Principal) -> str:
    return "agent" if p.token_type == "service" else "user"


def _check_request_rate(session: Session, settings: Settings, principal: Principal) -> None:
    since = utcnow() - timedelta(hours=1)
    count = session.scalar(
        select(func.count())
        .select_from(ApprovalRequest)
        .where(ApprovalRequest.requested_by == principal.actor_id, ApprovalRequest.created_wall >= since)
    )
    if (count or 0) >= settings.max_approval_requests_per_hour:
        raise rate_limited("approval_requests_per_hour_exceeded")


def _base_flags(principal: Principal) -> list[str]:
    return ["requested_by_agent"] if principal.token_type == "service" else []


def _pending_for_target(session: Session, target_type: str, target_id: str) -> ApprovalRequest | None:
    return session.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.target_type == target_type,
            ApprovalRequest.target_id == target_id,
            ApprovalRequest.status == ApprovalStatus.PENDING.value,
        )
    ).scalar_one_or_none()


def _new_request(
    session: Session,
    settings: Settings,
    principal: Principal,
    *,
    kind: str,
    target_type: str,
    target_id: str,
    target_version: int,
    reason_code: str,
    justification: str,
    payload: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    flags: list[str],
    source_context: dict[str, Any],
    abs_delta: int = 0,
    has_evidence: bool = False,
) -> ApprovalRequest:
    now = utcnow()
    req = ApprovalRequest(
        id=next_id(session, "APR"),
        kind=kind,
        status=ApprovalStatus.PENDING.value,  # zawsze pending — brak parametru, który by to zmienił
        requested_by=principal.actor_id,
        requested_via="mcp" if principal.token_type == "service" else "ui",
        target_type=target_type,
        target_id=target_id,
        target_version=target_version,
        reason_code=reason_code,
        justification=justification,
        payload=payload,
        before_state=before,
        after_state=after,
        flags=sorted(set(flags)),
        source_context=source_context,
        abs_delta=abs_delta,
        has_evidence=has_evidence,
        created_wall=now,
        expires_wall=now + timedelta(minutes=settings.approval_ttl_minutes),
    )
    session.add(req)
    session.flush()
    audit.append(
        session,
        actor=principal.actor_id,
        actor_type=_actor_type(principal),
        action=f"approval.request.{kind}",
        target=req.id,
        args={"target": target_id, "reason_code": reason_code, "flags": req.flags, **payload},
    )
    return req


def request_order_cancellation(
    session: Session,
    settings: Settings,
    principal: Principal,
    *,
    order_id: str,
    reason_code: str,
    justification: str,
) -> ApprovalRequest:
    if reason_code not in CANCEL_REASONS:
        raise validation("invalid_reason_code")
    _check_request_rate(session, settings, principal)
    order = session.get(Order, order_id)
    if order is None:
        raise not_found("order")
    if OrderStatus(order.status) not in CANCELLABLE_ORDER_STATES:
        raise invalid_state("order_not_cancellable", {"status": order.status})
    if _pending_for_target(session, "order", order_id):
        raise invalid_state("duplicate_pending_request")

    flags = _base_flags(principal)
    note = order.customer_note or ""
    if note and looks_like_instructions(note):
        flags.append("source_contains_instructions")
    if note and quotes_untrusted(justification, note):
        flags.append("justification_quotes_untrusted_text")
    if "suspicious" in (order.flags or []):
        flags.append("order_flagged_suspicious")
    if looks_like_instructions(justification):
        flags.append("justification_contains_instructions")

    req = _new_request(
        session,
        settings,
        principal,
        kind="order_cancellation",
        target_type="order",
        target_id=order.id,
        target_version=order.version,
        reason_code=reason_code,
        justification=justification,
        payload={},
        before={"status": order.status, "wave_id": order.wave_id, "lines": len(order.lines)},
        after={"status": OrderStatus.CANCELLED.value, "wave_id": None},
        flags=flags,
        source_context={
            "customer_ref": order.customer_ref,
            "customer_note": note[:1000],
            "note_signals": instruction_signals(note),
            "order_flags": list(order.flags or []),
        },
    )
    return req


def _resolve_evidence(session: Session, evidence_ref: str, location_id: str, sku_id: str) -> SimEvent:
    m = re.fullmatch(r"EVT-(\d{6,9})", evidence_ref)
    if not m:
        raise validation("invalid_evidence")
    event = session.get(SimEvent, int(m.group(1)))
    if (
        event is None
        or event.kind not in EVIDENCE_EVENT_KINDS
        or event.location_id != location_id
        or event.sku_id != sku_id
    ):
        raise validation("invalid_evidence")
    return event


def request_inventory_adjustment(
    session: Session,
    settings: Settings,
    principal: Principal,
    *,
    location_id: str,
    sku_id: str,
    counted_qty: int,
    reason_code: str,
    justification: str,
    evidence_ref: str | None,
) -> ApprovalRequest:
    if reason_code not in ADJUST_REASONS:
        raise validation("invalid_reason_code")
    if counted_qty < 0:
        raise validation("negative_quantity")
    _check_request_rate(session, settings, principal)
    stock = session.execute(
        select(StockItem).where(StockItem.location_id == location_id, StockItem.sku_id == sku_id)
    ).scalar_one_or_none()
    if stock is None:
        raise not_found("stock_item")
    delta = counted_qty - stock.qty
    if delta == 0:
        raise validation("no_change")
    if counted_qty < stock.reserved:
        raise invalid_state("counted_below_reserved", {"reserved": stock.reserved})
    if abs(delta) > settings.max_adjustment_delta_absolute:
        raise validation("delta_exceeds_absolute_limit")
    if _pending_for_target(session, "stock_item", str(stock.id)):
        raise invalid_state("duplicate_pending_request")

    flags = _base_flags(principal)
    evidence = None
    if evidence_ref:
        evidence = _resolve_evidence(session, evidence_ref, location_id, sku_id)
    else:
        limit = settings.max_adjustment_delta_without_evidence
        if abs(delta) > limit:
            raise validation("evidence_required", {"max_delta_without_evidence": limit})
        # Agregacja w oknie: wiele małych wniosków bez dowodu nie może łącznie przekroczyć limitu.
        since = utcnow() - timedelta(hours=settings.adjustment_window_hours)
        window_total = session.scalar(
            select(func.coalesce(func.sum(ApprovalRequest.abs_delta), 0)).where(
                ApprovalRequest.kind == "inventory_adjustment",
                ApprovalRequest.requested_by == principal.actor_id,
                ApprovalRequest.has_evidence.is_(False),
                ApprovalRequest.payload["sku_id"].as_string() == sku_id,
                ApprovalRequest.status.in_(LIVE_STATUSES),
                ApprovalRequest.created_wall >= since,
            )
        )
        if int(window_total or 0) + abs(delta) > limit:
            raise validation(
                "adjustment_window_limit_exceeded", {"window_hours": settings.adjustment_window_hours}
            )
        global_total = session.scalar(
            select(func.coalesce(func.sum(ApprovalRequest.abs_delta), 0)).where(
                ApprovalRequest.kind == "inventory_adjustment",
                ApprovalRequest.requested_by == principal.actor_id,
                ApprovalRequest.has_evidence.is_(False),
                ApprovalRequest.status.in_(LIVE_STATUSES),
                ApprovalRequest.created_wall >= since,
            )
        )
        if int(global_total or 0) + abs(delta) > 5 * limit:
            raise validation(
                "adjustment_window_limit_exceeded", {"window_hours": settings.adjustment_window_hours}
            )
    if abs(delta) > settings.max_adjustment_delta_without_evidence:
        flags.append("large_delta")
    if looks_like_instructions(justification):
        flags.append("justification_contains_instructions")
    sku_name = stock.sku.name if stock.sku else ""
    if sku_name and looks_like_instructions(sku_name):
        flags.append("source_contains_instructions")

    return _new_request(
        session,
        settings,
        principal,
        kind="inventory_adjustment",
        target_type="stock_item",
        target_id=str(stock.id),
        target_version=stock.version,
        reason_code=reason_code,
        justification=justification,
        payload={
            "location_id": location_id,
            "sku_id": sku_id,
            "counted_qty": counted_qty,
            "delta": delta,
            "evidence_ref": evidence_ref,
        },
        before={"qty": stock.qty, "reserved": stock.reserved},
        after={"qty": counted_qty, "reserved": stock.reserved},
        flags=flags,
        source_context={
            "sku_name": sku_name[:200],
            "evidence": {"ref": evidence.ref, "kind": evidence.kind, "details": evidence.details}
            if evidence
            else None,
        },
        abs_delta=abs(delta),
        has_evidence=evidence is not None,
    )


def expire_if_due(session: Session, req: ApprovalRequest) -> bool:
    if req.status == ApprovalStatus.PENDING.value and utcnow() >= req.expires_wall:
        req.status = transition(
            "approval", APPROVAL_TRANSITIONS, ApprovalStatus.PENDING, ApprovalStatus.EXPIRED
        ).value
        audit.append(session, actor="system", actor_type="system", action="approval.expired", target=req.id)
        return True
    return False


def expire_all_due(session: Session) -> int:
    due = (
        session.execute(
            select(ApprovalRequest).where(
                ApprovalRequest.status == ApprovalStatus.PENDING.value,
                ApprovalRequest.expires_wall <= utcnow(),
            )
        )
        .scalars()
        .all()
    )
    for req in due:
        expire_if_due(session, req)
    return len(due)


def _verify_manager_pin(session: Session, settings: Settings, principal: Principal, pin: str) -> User:
    user = session.get(User, principal.subject)
    if user is None or not user.active or user.role != Role.MANAGER.value:
        raise DomainError("FORBIDDEN", "manager_role_required", 403)
    if user.pin_failures >= settings.pin_max_failures:
        raise DomainError("FORBIDDEN", "reconfirmation_locked", 423)
    if not verify_secret(pin, user.pin_hash):
        user.pin_failures += 1
        audit.append(
            session,
            actor=principal.actor_id,
            actor_type="user",
            action="approval.reconfirm",
            decision="deny",
            outcome="denied",
            reason="invalid_pin",
        )
        session.commit()
        raise DomainError("FORBIDDEN", "reconfirmation_failed", 403)
    user.pin_failures = 0
    return user


def _remove_order_from_wave(session: Session, order: Order) -> None:
    if not order.wave_id:
        return
    wave = session.get(Wave, order.wave_id)
    if wave is None or wave.status != WaveStatus.VALIDATED.value:
        raise invalid_state("order_wave_not_modifiable")
    for asg in list(wave.assignments):
        if order.id in asg.order_ids:
            asg.order_ids = [o for o in asg.order_ids if o != order.id]
            # Trasa tego pracownika jest nieaktualna — usuwamy ją; agent musi ją przeliczyć.
            route = session.execute(
                select(PickRoute).where(PickRoute.wave_id == wave.id, PickRoute.worker_id == asg.worker_id)
            ).scalar_one_or_none()
            if route is not None:
                session.delete(route)
            if not asg.order_ids:
                session.delete(asg)
    wave.total_lines = max(0, wave.total_lines - len(order.lines))


def _execute(session: Session, req: ApprovalRequest) -> str | None:
    """Wykonuje operację nieodwracalną. Zwraca None (sukces) albo powód porażki."""
    if req.kind == "order_cancellation":
        order = session.get(Order, req.target_id)
        if order is None:
            return "target_missing"
        if order.version != req.target_version:
            return "stale_state"
        if OrderStatus(order.status) not in CANCELLABLE_ORDER_STATES:
            return "stale_state"
        _remove_order_from_wave(session, order)
        order.status = transition(
            "order", ORDER_TRANSITIONS, OrderStatus(order.status), OrderStatus.CANCELLED
        ).value
        order.wave_id = None
        order.worker_id = None
        return None
    if req.kind == "inventory_adjustment":
        stock = session.get(StockItem, int(req.target_id))
        if stock is None:
            return "target_missing"
        if stock.version != req.target_version:
            return "stale_state"
        counted = int(req.payload["counted_qty"])
        if counted < stock.reserved:
            return "stale_state"
        stock.qty = counted
        if stock.sim_physical_qty is not None and stock.sim_physical_qty == counted:
            stock.sim_physical_qty = None
        return None
    return "unknown_kind"


def decide(
    session: Session,
    settings: Settings,
    principal: Principal,
    *,
    request_id: str,
    decision: str,
    comment: str,
    pin: str,
    expected_version: int,
) -> ApprovalRequest:
    if (
        principal.token_type != "user"
        or principal.role is not Role.MANAGER
        or not principal.has("approvals:decide")
    ):
        raise DomainError("FORBIDDEN", "manager_role_required", 403)
    if decision not in {"approve", "reject"}:
        raise validation("invalid_decision")
    _verify_manager_pin(session, settings, principal, pin)

    req = session.get(ApprovalRequest, request_id)
    if req is None:
        raise not_found("approval_request")
    if expire_if_due(session, req):
        session.commit()
        raise invalid_state("request_expired")
    if req.status != ApprovalStatus.PENDING.value:
        raise invalid_state("not_pending", {"status": req.status})
    if req.version != expected_version:
        raise invalid_state("version_mismatch", {"current_version": req.version})
    if req.requested_by == principal.actor_id:
        raise DomainError("FORBIDDEN", "separation_of_duties", 403)

    now = utcnow()
    req.decided_by = principal.actor_id
    req.decided_wall = now
    req.decision_comment = comment
    if decision == "reject":
        req.status = transition(
            "approval", APPROVAL_TRANSITIONS, ApprovalStatus.PENDING, ApprovalStatus.REJECTED
        ).value
        audit.append(
            session, actor=principal.actor_id, actor_type="user", action="approval.reject", target=req.id
        )
        session.commit()
        return req

    req.status = transition(
        "approval", APPROVAL_TRANSITIONS, ApprovalStatus.PENDING, ApprovalStatus.APPROVED
    ).value
    failure: str | None
    try:
        failure = _execute(session, req)
        if failure is None:
            req.status = transition(
                "approval", APPROVAL_TRANSITIONS, ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED
            ).value
            req.executed_wall = now
            audit.append(
                session,
                actor=principal.actor_id,
                actor_type="user",
                action=f"approval.execute.{req.kind}",
                target=req.id,
                args={"target": req.target_id, "after": req.after_state},
            )
            session.commit()  # wniosek + zmiana celu + audyt: jedna transakcja
            return req
    except StaleDataError:
        # Równoległa zmiana celu lub wniosku między odczytem a zapisem (version_id_col).
        failure = "stale_state"

    # Porażka wykonania: wycofujemy wszystkie zmiany celu i zapisujemy status `failed`.
    session.rollback()
    fresh = session.get(ApprovalRequest, request_id)
    if fresh is None or fresh.status != ApprovalStatus.PENDING.value:
        raise invalid_state("not_pending", {"status": fresh.status if fresh else None})
    fresh.status = ApprovalStatus.FAILED.value  # approved -> failed (stan pośredni nie został utrwalony)
    fresh.failure_reason = failure
    fresh.decided_by = principal.actor_id
    fresh.decided_wall = now
    fresh.decision_comment = comment
    audit.append(
        session,
        actor=principal.actor_id,
        actor_type="user",
        action=f"approval.execute.{fresh.kind}",
        target=fresh.id,
        decision="deny",
        outcome="error",
        reason=str(failure),
    )
    session.commit()
    raise invalid_state(str(failure), {"request_status": ApprovalStatus.FAILED.value})


def to_public(req: ApprovalRequest, *, include_context: bool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "request_id": req.id,
        "kind": req.kind,
        "status": req.status,
        "target_type": req.target_type,
        "target_id": req.target_id,
        "reason_code": req.reason_code,
        "flags": req.flags,
        "created_at": req.created_wall.isoformat(),
        "expires_at": req.expires_wall.isoformat(),
        "decided_at": req.decided_wall.isoformat() if req.decided_wall else None,
        "failure_reason": req.failure_reason,
        "version": req.version,
    }
    if include_context:
        data.update(
            {
                "requested_by": req.requested_by,
                "requested_via": req.requested_via,
                "justification": req.justification,
                "payload": req.payload,
                "before_state": req.before_state,
                "after_state": req.after_state,
                "source_context": req.source_context,
                "decided_by": req.decided_by,
                "decision_comment": req.decision_comment,
                "target_version": req.target_version,
            }
        )
    return data
