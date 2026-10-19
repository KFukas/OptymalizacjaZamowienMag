"""KPI i wąskie gardła (liczone z danych WMS i zdarzeń symulacji)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from optimizer.layout import WarehouseLayout
from wms.deps import sim_now
from wms.layout import layout_from_db
from wms.models import Order, PickRoute, Shift, SimEvent, SimState, Wave, Worker
from wms.states import ACTIVE_WAVE_STATES, OrderStatus, RouteStatus

WINDOWS = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "shift": timedelta(hours=8),
    "day": timedelta(hours=24),
}
PACK_QUEUE_ALERT = 6
CONGESTION_ALERT = 3


def _on_shift(worker: Worker, shifts: dict[str, Shift], now: datetime) -> bool:
    sh = shifts.get(worker.shift_id)
    return bool(worker.active and sh and sh.start_hour <= now.hour < sh.end_hour)


def worker_aisle(layout: WarehouseLayout, x: float, y: float) -> int | None:
    if y <= layout.front_y + 0.5 or y >= layout.back_y - 0.5:
        return None
    return layout.aisle_of_x(round(x, 6))


def compute_kpis(session: Session, window: str = "1h") -> dict[str, Any]:
    now = sim_now(session)
    span = WINDOWS[window]
    since = now - span
    state = session.get(SimState, 1)
    day_start = state.day_start if state else now - span
    since = max(since, day_start) if window != "day" else since
    hours = max((now - since).total_seconds() / 3600.0, 1 / 60)
    layout = layout_from_db(session)

    events = (
        session.execute(select(SimEvent).where(SimEvent.ts_sim > since, SimEvent.ts_sim <= now))
        .scalars()
        .all()
    )
    picks = [e for e in events if e.kind == "pick"]
    stockouts = [e for e in events if e.kind == "stockout"]
    shipped = [e for e in events if e.kind == "shipped"]
    otif = (
        sum(1 for e in shipped if e.details.get("on_time") and e.details.get("in_full")) / len(shipped)
        if shipped
        else None
    )
    lines_by_worker = Counter(e.worker_id for e in picks)
    picks_by_aisle = Counter(int(e.location_id[5:7]) for e in picks if e.location_id)

    shifts = {s.id: s for s in session.execute(select(Shift)).scalars()}
    workers = session.execute(select(Worker)).scalars().all()
    on_shift = [w for w in workers if _on_shift(w, shifts, now)]
    busy = [w for w in on_shift if w.state in ("walking", "picking")]
    congestion = Counter(a for w in busy if (a := worker_aisle(layout, w.pos_x, w.pos_y)) is not None)

    routes_done = (
        session.execute(select(PickRoute).where(PickRoute.status == RouteStatus.DONE.value)).scalars().all()
    )
    dist_per_order = None
    if routes_done:
        orders_in_routes = sum(
            len({s.get("order_id") for s in r.stops if s.get("order_id")}) for r in routes_done
        )
        dist_per_order = sum(r.distance_m for r in routes_done) / max(orders_in_routes, 1)

    status_counts: dict[str, int] = {
        str(k): int(v) for k, v in session.execute(select(Order.status, func.count()).group_by(Order.status))
    }
    open_orders = int(status_counts.get(OrderStatus.OPEN.value, 0))
    at_risk = session.scalar(
        select(func.count())
        .select_from(Order)
        .where(Order.status == OrderStatus.OPEN.value, Order.due_at <= now + timedelta(hours=2))
    )
    overdue = session.scalar(
        select(func.count())
        .select_from(Order)
        .where(
            Order.status.in_([OrderStatus.OPEN.value, OrderStatus.ASSIGNED.value, OrderStatus.PICKING.value]),
            Order.due_at < now,
        )
    )
    wave_counts: dict[str, int] = {
        str(k): int(v) for k, v in session.execute(select(Wave.status, func.count()).group_by(Wave.status))
    }
    active_waves = sum(int(wave_counts.get(s.value, 0)) for s in ACTIVE_WAVE_STATES)
    pack_queue = state.pack_queue if state else 0

    alerts: list[dict[str, Any]] = []
    if pack_queue >= PACK_QUEUE_ALERT:
        alerts.append(
            {"kind": "pack_queue", "severity": "high", "message": f"Kolejka przy pakowaniu: {pack_queue}"}
        )
    if at_risk:
        alerts.append(
            {
                "kind": "orders_at_risk",
                "severity": "medium",
                "message": f"{at_risk} otwartych zamówień z terminem < 2 h",
            }
        )
    if overdue:
        alerts.append({"kind": "overdue", "severity": "high", "message": f"{overdue} zamówień po terminie"})
    for aisle, n in congestion.items():
        if n >= CONGESTION_ALERT:
            alerts.append(
                {"kind": "congestion", "severity": "medium", "message": f"Zator w alejce {aisle}: {n} osób"}
            )
    if stockouts:
        alerts.append(
            {"kind": "stockouts", "severity": "medium", "message": f"{len(stockouts)} braków towaru w oknie"}
        )
    idle = [w.id for w in on_shift if w.state == "idle"]
    if idle and open_orders > 0:
        alerts.append(
            {
                "kind": "idle_workers",
                "severity": "low",
                "message": f"{len(idle)} pracowników bez zadania przy {open_orders} otwartych zamówieniach",
            }
        )

    return {
        "sim_now": now.isoformat(),
        "window": window,
        "window_hours": round(hours, 3),
        "lines_picked": len(picks),
        "lines_per_hour": round(len(picks) / hours, 1),
        "lines_per_hour_per_worker": round(len(picks) / hours / max(len(on_shift), 1), 1),
        "distance_per_order_m": round(dist_per_order, 1) if dist_per_order is not None else None,
        "otif": round(otif, 3) if otif is not None else None,
        "shipped": len(shipped),
        "utilization": round(len(busy) / len(on_shift), 3) if on_shift else 0.0,
        "workers_on_shift": len(on_shift),
        "workers_busy": len(busy),
        "pack_queue": pack_queue,
        "open_orders": open_orders,
        "orders_at_risk": int(at_risk or 0),
        "orders_overdue": int(overdue or 0),
        "orders_by_status": {k: int(v) for k, v in status_counts.items()},
        "waves_by_status": {k: int(v) for k, v in wave_counts.items()},
        "active_waves": active_waves,
        "stockouts": [
            {
                "event_ref": e.ref,
                "ts": e.ts_sim.isoformat(),
                "location_id": e.location_id,
                "sku_id": e.sku_id,
                "need": e.details.get("need"),
                "found": e.details.get("found"),
            }
            for e in stockouts[-50:]
        ],
        "lines_by_worker": dict(lines_by_worker),
        "picks_by_aisle": {str(k): v for k, v in sorted(picks_by_aisle.items())},
        "congestion_by_aisle": {str(k): v for k, v in sorted(congestion.items())},
        "alerts": alerts,
    }
