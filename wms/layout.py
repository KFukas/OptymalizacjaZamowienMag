"""Budowa geometrii magazynu (optimizer.layout.WarehouseLayout) na podstawie danych w bazie."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from optimizer.layout import AISLE_SPACING_M, RACK_START_Y, WarehouseLayout
from wms.models import Location, Zone

_cache: dict[tuple[int, int, int, str], WarehouseLayout] = {}


def layout_from_db(session: Session) -> WarehouseLayout:
    zones = session.execute(select(Zone).order_by(Zone.id)).scalars().all()
    aisle_zone = {a: z.id for z in zones for a in range(z.aisle_from, z.aisle_to + 1)}
    bays = session.scalar(select(func.max(Location.bay))) or 0
    levels = session.scalar(select(func.max(Location.level))) or 0
    key = (
        len(aisle_zone),
        int(bays),
        int(levels),
        "".join(aisle_zone.get(a, "") for a in sorted(aisle_zone)),
    )
    if key not in _cache:
        _cache[key] = WarehouseLayout(len(aisle_zone), int(bays), int(levels), aisle_zone)
    return _cache[key]


def layout_geometry(session: Session) -> dict[str, Any]:
    lay = layout_from_db(session)
    return {
        "aisle_count": lay.aisle_count,
        "bays": lay.bays,
        "levels": lay.levels,
        "width": round(lay.width, 2),
        "front_y": lay.front_y,
        "back_y": round(lay.back_y, 2),
        "rack_start_y": RACK_START_Y,
        "aisle_spacing": AISLE_SPACING_M,
        "depot": {"x": lay.depot[0], "y": lay.depot[1]},
        "aisles": [
            {"aisle": a, "x": lay.aisle_x(a), "zone": lay.aisle_zone.get(a, "?")}
            for a in range(1, lay.aisle_count + 1)
        ],
    }
