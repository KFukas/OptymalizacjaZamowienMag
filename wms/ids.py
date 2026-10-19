"""Generatory identyfikatorów domenowych i ich wzorce (wspólne dla WMS i mcp-wms)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

ORDER_ID = r"^ORD-[0-9]{8}$"
SKU_ID = r"^SKU-[0-9]{6}$"
LOCATION_ID = r"^LOC-[A-Z][0-9]{2}-[0-9]{2}-[0-9]$"
WORKER_ID = r"^WRK-[0-9]{3}$"
WAVE_ID = r"^WAVE-[0-9]{6}$"
APPROVAL_ID = r"^APR-[0-9]{6}$"
RESLOTTING_ID = r"^RSL-[0-9]{6}$"
SHIFT_ID = r"^SHIFT-[0-9]$"
EVENT_REF = r"^EVT-[0-9]{6,9}$"
IDEMPOTENCY_KEY = r"^[A-Za-z0-9_-]{8,64}$"

_WIDTH = {"ORD": 8, "WAVE": 6, "APR": 6, "RSL": 6}


def next_id(session: Session, prefix: str) -> str:
    from wms.models import IdCounter

    counter = session.execute(
        select(IdCounter).where(IdCounter.name == prefix).with_for_update()
    ).scalar_one_or_none()
    if counter is None:
        counter = IdCounter(name=prefix, value=0)
        session.add(counter)
    counter.value += 1
    session.flush()
    return f"{prefix}-{counter.value:0{_WIDTH[prefix]}d}"


def location_id(zone: str, aisle: int, bay: int, level: int) -> str:
    return f"LOC-{zone}{aisle:02d}-{bay:02d}-{level}"
