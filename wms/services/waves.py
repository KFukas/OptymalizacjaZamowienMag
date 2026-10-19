"""Fale kompletacji: walidacja propozycji, trasy, zwolnienie, odrzucenie propozycji.

Propozycja agenta jest weryfikowana w całości po stronie WMS:
- zamówienia istnieją, są `open` i nie należą do żadnej fali,
- pracownicy istnieją, są aktywni, na zmianie i nie są zajęci w innej aktywnej fali,
- cut-off w przyszłości (≤ 12 h), towar dostępny (z uwzględnieniem alokacji innych propozycji),
- podział na kursy mieści się w pojemności wózków, a szacowany czas pracy — przed cut-off / końcem zmiany.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from optimizer.batching import OrderSpec, WorkerSpec, plan_batches
from optimizer.routing import Algorithm, PickItem, gain_pct
from wms import audit
from wms.auth import Principal
from wms.config import Settings
from wms.deps import sim_now
from wms.errors import invalid_state, not_found, rate_limited, validation
from wms.ids import next_id
from wms.layout import layout_from_db
from wms.models import (
    Location,
    Order,
    OrderLine,
    PickRoute,
    Shift,
    Sku,
    StockItem,
    Wave,
    WaveAssignment,
    Worker,
)
from wms.optimizer_client import solve_route
from wms.security import utcnow
from wms.states import (
    ACTIVE_WAVE_STATES,
    ORDER_TRANSITIONS,
    WAVE_TRANSITIONS,
    OrderStatus,
    RouteStatus,
    WaveStatus,
    transition,
)
from wms.views import wave_view

MAX_CUTOFF_AHEAD = timedelta(hours=12)


def _actor_type(p: Principal) -> str:
    return "agent" if p.token_type == "service" else "user"


def _shift_end(now: datetime, shift: Shift) -> datetime:
    return now.replace(hour=shift.end_hour % 24, minute=0, second=0, microsecond=0) + (
        timedelta(days=1) if shift.end_hour >= 24 else timedelta(0)
    )


def _soft_allocations(session: Session) -> dict[tuple[str, str], int]:
    """Ilości przypisane do zwalidowanych (jeszcze niezarezerwowanych) propozycji fal."""
    rows = session.execute(
        select(OrderLine.location_id, OrderLine.sku_id, func.sum(OrderLine.qty))
        .join(Order, Order.id == OrderLine.order_id)
        .where(Order.status == OrderStatus.ASSIGNED.value)
        .group_by(OrderLine.location_id, OrderLine.sku_id)
    ).all()
    return {(loc, sku): int(q or 0) for loc, sku, q in rows if loc}


def _allocate(session: Session, orders: list[Order]) -> tuple[dict[int, str], list[str]]:
    soft = _soft_allocations(session)
    stock_by_sku: dict[str, list[StockItem]] = defaultdict(list)
    skus = {ln.sku_id for o in orders for ln in o.lines}
    for item in session.execute(select(StockItem).where(StockItem.sku_id.in_(skus))).scalars():
        stock_by_sku[item.sku_id].append(item)
    taken: dict[tuple[str, str], int] = defaultdict(int)
    plan: dict[int, str] = {}
    short_orders: list[str] = []
    for o in sorted(orders, key=lambda x: (x.due_at, x.id)):
        local: dict[int, str] = {}
        ok = True
        for ln in o.lines:
            best = None
            best_avail = -1
            for it in stock_by_sku.get(ln.sku_id, []):
                key = (it.location_id, it.sku_id)
                avail = it.qty - it.reserved - soft.get(key, 0) - taken[key]
                if avail >= ln.qty and (
                    avail > best_avail or (avail == best_avail and it.location_id < (best or ""))
                ):
                    best, best_avail = it.location_id, avail
            if best is None:
                ok = False
                break
            local[ln.id] = best
        if not ok:
            short_orders.append(o.id)
            continue
        for ln in o.lines:
            taken[(local[ln.id], ln.sku_id)] += ln.qty
        plan.update(local)
    return plan, short_orders


def _pick_items(
    session: Session, orders: list[Order], alloc: dict[int, str] | None = None
) -> dict[str, list[PickItem]]:
    loc_ids = {(alloc or {}).get(ln.id, ln.location_id) for o in orders for ln in o.lines}
    locs = {
        loc.id: loc for loc in session.execute(select(Location).where(Location.id.in_(loc_ids))).scalars()
    }
    vols = {
        s.id: s.volume_l
        for s in session.execute(
            select(Sku).where(Sku.id.in_({ln.sku_id for o in orders for ln in o.lines}))
        ).scalars()
    }
    out: dict[str, list[PickItem]] = {}
    for o in orders:
        items = []
        for ln in o.lines:
            loc_id = (alloc or {}).get(ln.id, ln.location_id)
            if loc_id is None:
                raise invalid_state("line_without_location", {"order_id": o.id})
            loc = locs[loc_id]
            items.append(
                PickItem(loc.id, ln.sku_id, ln.qty, loc.x, loc.y, o.id, ln.id, vols.get(ln.sku_id, 0.0))
            )
        out[o.id] = items
    return out


def _check_rate(session: Session, settings: Settings, principal: Principal) -> None:
    since = utcnow() - timedelta(hours=1)
    n = session.scalar(
        select(func.count())
        .select_from(Wave)
        .where(Wave.created_by == principal.actor_id, Wave.created_wall >= since)
    )
    if (n or 0) >= settings.max_wave_proposals_per_hour:
        raise rate_limited("wave_proposals_per_hour_exceeded")


def propose_wave(
    session: Session,
    settings: Settings,
    principal: Principal,
    *,
    order_ids: list[str],
    worker_ids: list[str],
    cutoff: datetime,
    rationale: str,
    dry_run: bool,
) -> dict[str, Any]:
    if len(set(order_ids)) != len(order_ids) or len(set(worker_ids)) != len(worker_ids):
        raise validation("duplicate_ids")
    cutoff = cutoff.replace(tzinfo=None)
    now = sim_now(session)
    if cutoff <= now:
        raise validation("cutoff_in_past", {"sim_now": now.isoformat()})
    if cutoff > now + MAX_CUTOFF_AHEAD:
        raise validation("cutoff_too_far")
    if not dry_run:
        _check_rate(session, settings, principal)

    orders = session.execute(select(Order).where(Order.id.in_(order_ids))).scalars().all()
    if len(orders) != len(order_ids):
        missing = sorted(set(order_ids) - {o.id for o in orders})
        raise validation("orders_not_found", {"order_ids": missing[:20]})
    not_open = sorted(o.id for o in orders if o.status != OrderStatus.OPEN.value or o.wave_id is not None)
    if not_open:
        raise invalid_state("orders_not_open_or_in_other_wave", {"order_ids": not_open[:20]})

    workers = session.execute(select(Worker).where(Worker.id.in_(worker_ids))).scalars().all()
    if len(workers) != len(worker_ids):
        raise validation(
            "workers_not_found", {"worker_ids": sorted(set(worker_ids) - {w.id for w in workers})}
        )
    shifts = {s.id: s for s in session.execute(select(Shift)).scalars()}
    busy_ids = set(
        session.scalars(
            select(WaveAssignment.worker_id)
            .join(Wave, Wave.id == WaveAssignment.wave_id)
            .where(
                Wave.status.in_([s.value for s in ACTIVE_WAVE_STATES]),
                WaveAssignment.worker_id.in_(worker_ids),
            )
        )
    )
    specs: list[WorkerSpec] = []
    off: list[str] = []
    for w in sorted(workers, key=lambda x: x.id):
        sh = shifts.get(w.shift_id)
        if not w.active or sh is None or not (sh.start_hour <= now.hour < sh.end_hour):
            off.append(w.id)
            continue
        end = min(cutoff, _shift_end(now, sh))
        specs.append(
            WorkerSpec(
                w.id, w.speed_mps, w.pick_time_s, w.efficiency, w.cart_capacity_l, w.cart_max_orders,
                available_s=(end - now).total_seconds(),
            )
        )  # fmt: skip
    if off:
        raise invalid_state("workers_not_on_shift", {"worker_ids": off})
    if busy_ids:
        raise invalid_state("workers_busy_in_active_wave", {"worker_ids": sorted(busy_ids)})

    alloc, short = _allocate(session, list(orders))
    if short:
        raise invalid_state("insufficient_stock", {"order_ids": sorted(short)[:20]})
    items = _pick_items(session, list(orders), alloc)
    specs_orders = [
        OrderSpec(o.id, tuple(items[o.id]), (o.due_at - now).total_seconds(), o.priority) for o in orders
    ]
    layout = layout_from_db(session)
    plan = plan_batches(layout, specs_orders, specs)
    if not plan.feasible:
        raise validation("wave_infeasible", {"problems": plan.problems[:10]})

    total_lines = sum(len(o.lines) for o in orders)
    result: dict[str, Any] = {
        "dry_run": dry_run,
        "checks": [
            "orders_open_and_unassigned",
            "workers_on_shift_and_free",
            "cutoff_valid",
            "stock_available",
            "cart_capacity",
            "time_before_cutoff",
        ],
        "plan": [
            {
                "worker_id": p.worker_id,
                "trips": p.trips,
                "est_distance_m": round(p.est_distance_m, 1),
                "est_duration_s": round(p.est_duration_s),
                "available_s": round(p.available_s),
            }
            for p in plan.workers
        ],
        "est_distance_m": round(plan.est_distance_m, 1),
        "makespan_s": round(plan.makespan_s),
        "total_lines": total_lines,
    }
    if dry_run:
        session.rollback()
        result["wave_id"] = None
        return result

    wave = Wave(
        id=next_id(session, "WAVE"),
        status=WaveStatus.VALIDATED.value,
        created_by=principal.actor_id,
        rationale=rationale,
        cutoff_at=cutoff,
        created_at=now,
        created_wall=utcnow(),
        est_distance_m=plan.est_distance_m,
        est_duration_s=plan.makespan_s,
        total_lines=total_lines,
    )
    session.add(wave)
    session.flush()
    by_id = {o.id: o for o in orders}
    for p in plan.workers:
        session.add(
            WaveAssignment(wave_id=wave.id, worker_id=p.worker_id, order_ids=p.order_ids, trips=p.trips)
        )
        for oid in p.order_ids:
            o = by_id[oid]
            o.status = transition(
                "order", ORDER_TRANSITIONS, OrderStatus(o.status), OrderStatus.ASSIGNED
            ).value
            o.wave_id = wave.id
            o.worker_id = p.worker_id
            for ln in o.lines:
                ln.location_id = alloc[ln.id]
    audit.append(
        session,
        actor=principal.actor_id,
        actor_type=_actor_type(principal),
        action="wave.propose",
        target=wave.id,
        args={"orders": len(order_ids), "workers": worker_ids, "cutoff": cutoff.isoformat()},
    )
    session.flush()
    session.refresh(wave)
    result["wave_id"] = wave.id
    result["wave"] = wave_view(wave)
    return result


def compute_route(
    session: Session,
    settings: Settings,
    principal: Principal,
    *,
    wave_id: str,
    worker_id: str,
    algorithm: str,
) -> dict[str, Any]:
    wave = session.get(Wave, wave_id)
    if wave is None:
        raise not_found("wave")
    if wave.status != WaveStatus.VALIDATED.value:
        raise invalid_state("wave_not_validated", {"status": wave.status})
    asg = next((a for a in wave.assignments if a.worker_id == worker_id), None)
    if asg is None:
        raise not_found("assignment")
    worker = session.get(Worker, worker_id)
    assert worker is not None
    live = {o.id: o for o in session.execute(select(Order).where(Order.id.in_(asg.order_ids))).scalars()}
    trips_ids = [[oid for oid in t if oid in live] for t in (asg.trips or [asg.order_ids])]
    trips_ids = [t for t in trips_ids if t]
    items = _pick_items(session, list(live.values()))
    trips = [[it for oid in t for it in items[oid]] for t in trips_ids]
    layout = layout_from_db(session)
    sol = solve_route(settings, layout, trips, algorithm)
    baseline = (
        sol
        if algorithm == Algorithm.S_SHAPE.value
        else solve_route(settings, layout, trips, Algorithm.S_SHAPE.value)
    )
    duration = sol.duration_s(worker.speed_mps * worker.efficiency, worker.pick_time_s / worker.efficiency)
    route = session.execute(
        select(PickRoute).where(PickRoute.wave_id == wave_id, PickRoute.worker_id == worker_id)
    ).scalar_one_or_none()
    if route is None:
        route = PickRoute(
            wave_id=wave_id,
            worker_id=worker_id,
            algorithm=algorithm,
            distance_m=0,
            duration_s=0,
            created_at=sim_now(session),
        )
        session.add(route)
    route.algorithm = algorithm
    route.distance_m = sol.distance_m
    route.baseline_distance_m = baseline.distance_m
    route.duration_s = duration
    route.trips = len(sol.trips)
    route.stops = sol.stops(layout.depot)
    route.path = sol.path()
    route.status = RouteStatus.PLANNED.value
    route.progress_stop = 0
    session.flush()
    routes = session.execute(select(PickRoute).where(PickRoute.wave_id == wave_id)).scalars().all()
    if len(routes) == len(wave.assignments):
        wave.est_distance_m = sum(r.distance_m for r in routes)
        wave.est_duration_s = max(r.duration_s for r in routes)
    now = sim_now(session)
    audit.append(
        session,
        actor=principal.actor_id,
        actor_type=_actor_type(principal),
        action="route.compute",
        target=f"{wave_id}/{worker_id}",
        args={"algorithm": algorithm, "distance_m": round(sol.distance_m, 1)},
    )
    return {
        "wave_id": wave_id,
        "worker_id": worker_id,
        "algorithm": algorithm,
        "distance_m": round(sol.distance_m, 1),
        "baseline_s_shape_m": round(baseline.distance_m, 1),
        "gain_vs_s_shape_pct": gain_pct(baseline.distance_m, sol.distance_m),
        "est_duration_s": round(duration),
        "finishes_before_cutoff": now + timedelta(seconds=duration) <= wave.cutoff_at,
        "trips": len(sol.trips),
        "stops": sol.pick_count,
        "first_stops": [s["location_id"] for s in route.stops if s["type"] == "pick"][:10],
    }


def release_wave(
    session: Session, settings: Settings, principal: Principal, *, wave_id: str
) -> dict[str, Any]:
    wave = session.get(Wave, wave_id)
    if wave is None:
        raise not_found("wave")
    if wave.status != WaveStatus.VALIDATED.value:
        raise invalid_state("wave_not_validated", {"status": wave.status})
    now = sim_now(session)
    if wave.cutoff_at <= now:
        raise invalid_state("cutoff_passed")
    since = utcnow() - timedelta(hours=1)
    n = session.scalar(
        select(func.count())
        .select_from(Wave)
        .where(Wave.released_by == principal.actor_id, Wave.released_wall >= since)
    )
    if (n or 0) >= settings.max_wave_releases_per_hour:
        raise rate_limited("wave_releases_per_hour_exceeded")
    routes = {
        r.worker_id: r
        for r in session.execute(select(PickRoute).where(PickRoute.wave_id == wave_id)).scalars()
    }
    missing = sorted(a.worker_id for a in wave.assignments if a.order_ids and a.worker_id not in routes)
    if missing:
        raise invalid_state("routes_missing", {"worker_ids": missing})
    orders = session.execute(select(Order).where(Order.wave_id == wave_id)).scalars().all()
    for o in orders:
        if o.status != OrderStatus.ASSIGNED.value:
            raise invalid_state("order_state_changed", {"order_id": o.id})
        for ln in o.lines:
            item = session.execute(
                select(StockItem).where(
                    StockItem.location_id == ln.location_id, StockItem.sku_id == ln.sku_id
                )
            ).scalar_one_or_none()
            if item is None or item.qty - item.reserved < ln.qty:
                raise invalid_state("insufficient_stock_at_release", {"order_id": o.id})
            item.reserved += ln.qty
        o.status = transition("order", ORDER_TRANSITIONS, OrderStatus.ASSIGNED, OrderStatus.PICKING).value
    wave.status = transition("wave", WAVE_TRANSITIONS, WaveStatus.VALIDATED, WaveStatus.RELEASED).value
    wave.released_at = now
    wave.released_wall = utcnow()
    wave.released_by = principal.actor_id
    audit.append(
        session,
        actor=principal.actor_id,
        actor_type=_actor_type(principal),
        action="wave.release",
        target=wave_id,
        args={"orders": len(orders)},
    )
    session.flush()
    return {"wave_id": wave_id, "status": wave.status, "orders": len(orders), "released_at": now.isoformat()}


def discard_wave(session: Session, principal: Principal, *, wave_id: str) -> dict[str, Any]:
    wave = session.get(Wave, wave_id)
    if wave is None:
        raise not_found("wave")
    if wave.status != WaveStatus.VALIDATED.value:
        raise invalid_state("wave_not_validated", {"status": wave.status})
    for o in session.execute(select(Order).where(Order.wave_id == wave_id)).scalars():
        o.status = transition("order", ORDER_TRANSITIONS, OrderStatus.ASSIGNED, OrderStatus.OPEN).value
        o.wave_id = None
        o.worker_id = None
    for r in session.execute(select(PickRoute).where(PickRoute.wave_id == wave_id)).scalars():
        session.delete(r)
    wave.status = transition("wave", WAVE_TRANSITIONS, WaveStatus.VALIDATED, WaveStatus.DISCARDED).value
    audit.append(
        session,
        actor=principal.actor_id,
        actor_type=_actor_type(principal),
        action="wave.discard",
        target=wave_id,
    )
    session.flush()
    return {"wave_id": wave_id, "status": wave.status}
