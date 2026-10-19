"""Maszyny stanów encji domenowych. Każde przejście przechodzi przez `transition()`."""

from __future__ import annotations

from enum import StrEnum


class OrderStatus(StrEnum):
    OPEN = "open"
    ASSIGNED = "assigned"  # w zwalidowanej (niezwolnionej) propozycji fali
    PICKING = "picking"  # fala zwolniona
    PICKED = "picked"
    PACKED = "packed"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"


class WaveStatus(StrEnum):
    VALIDATED = "validated"  # propozycja agenta po walidacji serwera
    RELEASED = "released"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DISCARDED = "discarded"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"  # stan przejściowy w transakcji decyzji
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"  # zatwierdzony, ale stan celu się zmienił (optimistic locking)


class ReslottingStatus(StrEnum):
    PROPOSED = "proposed"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"


class RouteStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    DONE = "done"


ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.OPEN: {OrderStatus.ASSIGNED, OrderStatus.CANCELLED},
    OrderStatus.ASSIGNED: {OrderStatus.OPEN, OrderStatus.PICKING, OrderStatus.CANCELLED},
    OrderStatus.PICKING: {OrderStatus.PICKED},
    OrderStatus.PICKED: {OrderStatus.PACKED},
    OrderStatus.PACKED: {OrderStatus.SHIPPED},
    OrderStatus.SHIPPED: set(),
    OrderStatus.CANCELLED: set(),
}

WAVE_TRANSITIONS: dict[WaveStatus, set[WaveStatus]] = {
    WaveStatus.VALIDATED: {WaveStatus.RELEASED, WaveStatus.DISCARDED},
    WaveStatus.RELEASED: {WaveStatus.IN_PROGRESS, WaveStatus.COMPLETED},
    WaveStatus.IN_PROGRESS: {WaveStatus.COMPLETED},
    WaveStatus.COMPLETED: set(),
    WaveStatus.DISCARDED: set(),
}

APPROVAL_TRANSITIONS: dict[ApprovalStatus, set[ApprovalStatus]] = {
    ApprovalStatus.PENDING: {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED},
    ApprovalStatus.APPROVED: {ApprovalStatus.EXECUTED, ApprovalStatus.FAILED},
    ApprovalStatus.EXECUTED: set(),
    ApprovalStatus.REJECTED: set(),
    ApprovalStatus.EXPIRED: set(),
    ApprovalStatus.FAILED: set(),
}

RESLOTTING_TRANSITIONS: dict[ReslottingStatus, set[ReslottingStatus]] = {
    ReslottingStatus.PROPOSED: {ReslottingStatus.APPLIED, ReslottingStatus.REJECTED, ReslottingStatus.FAILED},
    ReslottingStatus.APPLIED: set(),
    ReslottingStatus.REJECTED: set(),
    ReslottingStatus.FAILED: set(),
}

CANCELLABLE_ORDER_STATES = {OrderStatus.OPEN, OrderStatus.ASSIGNED}
ACTIVE_WAVE_STATES = {WaveStatus.VALIDATED, WaveStatus.RELEASED, WaveStatus.IN_PROGRESS}


class InvalidTransition(Exception):
    def __init__(self, entity: str, current: str, target: str) -> None:
        super().__init__(f"{entity}: {current} -> {target} niedozwolone")
        self.entity = entity
        self.current = current
        self.target = target


def can_transition[S: StrEnum](table: dict[S, set[S]], current: S, target: S) -> bool:
    return target in table.get(current, set())


def transition[S: StrEnum](entity: str, table: dict[S, set[S]], current: S, target: S) -> S:
    if not can_transition(table, current, target):
        raise InvalidTransition(entity, str(current), str(target))
    return target
