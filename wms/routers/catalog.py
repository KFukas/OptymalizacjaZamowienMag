"""Odczyt: zamówienia, zapasy, układ magazynu, pracownicy."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from wms import ids
from wms.auth import Principal, require_scope
from wms.deps import get_session, sim_now
from wms.errors import not_found, validation
from wms.models import Location, Order, OrderLine, Shift, StockItem, Worker, Zone
from wms.views import location_view, order_view, stock_view, worker_view, zone_view

router = APIRouter(tags=["catalog"])


@router.get("/orders")
def list_orders(
    status: Annotated[
        Literal["open", "assigned", "picking", "picked", "packed", "shipped", "cancelled"] | None, Query()
    ] = None,
    priority: Annotated[Literal["express", "standard", "economy"] | None, Query()] = None,
    due_before: datetime | None = None,
    zone: Annotated[str | None, Query(pattern=r"^[A-Z]$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_scope("orders:read")),
) -> dict:
    q = select(Order)
    if status:
        q = q.where(Order.status == status)
    if priority:
        q = q.where(Order.priority == priority)
    if due_before:
        q = q.where(Order.due_at <= due_before)
    if zone:
        q = q.where(
            Order.id.in_(select(OrderLine.order_id).where(OrderLine.location_id.like(f"LOC-{zone}%")))
        )
    q = q.order_by(Order.due_at, Order.id).offset(offset).limit(limit)
    orders = session.execute(q).scalars().all()
    return {
        "sim_now": sim_now(session).isoformat(),
        "count": len(orders),
        "orders": [order_view(o, with_lines=False) for o in orders],
    }


@router.get("/orders/{order_id}")
def get_order(
    order_id: Annotated[str, Path(pattern=ids.ORDER_ID)],
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_scope("orders:read")),
) -> dict:
    order = session.get(Order, order_id)
    if order is None:
        raise not_found("order")
    return order_view(order)


@router.get("/inventory")
def get_inventory(
    sku_id: Annotated[str | None, Query(pattern=ids.SKU_ID)] = None,
    location_id: Annotated[str | None, Query(pattern=ids.LOCATION_ID)] = None,
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_scope("inventory:read")),
) -> dict:
    if bool(sku_id) == bool(location_id):
        raise validation("exactly_one_of_sku_id_location_id")
    q = select(StockItem)
    q = q.where(StockItem.sku_id == sku_id) if sku_id else q.where(StockItem.location_id == location_id)
    items = session.execute(q.order_by(StockItem.location_id)).scalars().all()
    return {"items": [stock_view(s) for s in items]}


@router.get("/layout")
def get_layout(
    include_locations: bool = True,
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_scope("layout:read")),
) -> dict:
    from wms.layout import layout_geometry

    zones = session.execute(select(Zone).order_by(Zone.id)).scalars().all()
    data = {"zones": [zone_view(z) for z in zones], "geometry": layout_geometry(session)}
    if include_locations:
        locs = session.execute(select(Location).order_by(Location.id)).scalars().all()
        data["locations"] = [location_view(loc) for loc in locs]
    return data


@router.get("/workers")
def list_workers(
    shift_id: Annotated[str | None, Query(pattern=ids.SHIFT_ID)] = None,
    session: Session = Depends(get_session),
    _p: Principal = Depends(require_scope("workers:read")),
) -> dict:
    q = select(Worker).order_by(Worker.id)
    if shift_id:
        q = q.where(Worker.shift_id == shift_id)
    workers = session.execute(q).scalars().all()
    now = sim_now(session)
    shifts = {s.id: s for s in session.execute(select(Shift)).scalars()}
    out = []
    for w in workers:
        v = worker_view(w)
        sh = shifts.get(w.shift_id)
        v["on_shift"] = bool(w.active and sh and sh.start_hour <= now.hour < sh.end_hour)
        out.append(v)
    return {"sim_now": now.isoformat(), "workers": out}
