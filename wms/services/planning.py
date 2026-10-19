"""Prognoza popytu, analiza slottingu, propozycje przesunięć (re-slotting), sugestie fal, heatmapa."""

from __future__ import annotations

import threading
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from optimizer.forecast import forecast_sku
from optimizer.slotting import (
    SlotLocation,
    SlotSku,
    SlotStock,
    classify,
    compatible,
    fits,
    location_cost,
    propose_moves,
)
from optimizer.waves import AvailableWorker, OpenOrder, suggest_waves
from wms import audit
from wms.auth import Principal
from wms.deps import sim_now
from wms.errors import DomainError, invalid_state, not_found, validation
from wms.ids import next_id
from wms.layout import layout_from_db
from wms.models import (
    Location,
    Order,
    OrderLine,
    ReslottingProposal,
    SalesHistory,
    Shift,
    Sku,
    StockItem,
    Wave,
    WaveAssignment,
    Worker,
)
from wms.security import utcnow
from wms.states import ACTIVE_WAVE_STATES, RESLOTTING_TRANSITIONS, OrderStatus, ReslottingStatus, transition

DEMAND_WINDOW_DAYS = 28
_forecast_cache: dict[tuple[str, int, date], dict] = {}
_cache_lock = threading.Lock()


def _history(
    session: Session, sku_ids: list[str], days: int | None = None
) -> tuple[dict[str, np.ndarray], date | None]:
    last = session.scalar(select(func.max(SalesHistory.day)))
    if last is None:
        return {s: np.zeros(0) for s in sku_ids}, None
    first = session.scalar(select(func.min(SalesHistory.day)))
    assert first is not None
    start = first if days is None else max(first, last - timedelta(days=days - 1))
    n = (last - start).days + 1
    out = {s: np.zeros(n) for s in sku_ids}
    q = select(SalesHistory.sku_id, SalesHistory.day, SalesHistory.qty).where(SalesHistory.day >= start)
    if len(sku_ids) <= 900:
        q = q.where(SalesHistory.sku_id.in_(sku_ids))
    for sku, d, qty in session.execute(q):
        if sku in out:
            out[sku][(d - start).days] = qty
    return out, last


def forecast(session: Session, sku_ids: list[str], horizon: int) -> dict[str, Any]:
    if len(set(sku_ids)) != len(sku_ids):
        raise validation("duplicate_ids")
    known = set(session.scalars(select(Sku.id).where(Sku.id.in_(sku_ids))))
    unknown = sorted(set(sku_ids) - known)
    if unknown:
        raise validation("skus_not_found", {"sku_ids": unknown[:20]})
    hist, last = _history(session, sku_ids)
    results = []
    for sku in sku_ids:
        key = (sku, horizon, last or date.min)
        with _cache_lock:
            cached = _forecast_cache.get(key)
        if cached is None:
            cached = forecast_sku(sku, hist[sku], horizon).as_dict()
            with _cache_lock:
                _forecast_cache[key] = cached
        results.append(cached)
    weighted = [(r["wape_backtest"], r["mean_daily"]) for r in results if r["wape_backtest"] is not None]
    den = sum(m for _, m in weighted)
    agg = sum(w * m for w, m in weighted) / den if den else None
    return {
        "horizon_days": horizon,
        "history_until": last.isoformat() if last else None,
        "results": results,
        "aggregate_wape_backtest": None if agg is None else round(agg, 3),
    }


def _active_locations(session: Session) -> set[str]:
    rows = session.scalars(
        select(OrderLine.location_id)
        .join(Order, Order.id == OrderLine.order_id)
        .where(Order.status.in_([OrderStatus.ASSIGNED.value, OrderStatus.PICKING.value]))
    )
    return {loc for loc in rows if loc}


def _slot_data(session: Session) -> tuple[list[SlotLocation], dict[str, SlotSku], list[SlotStock]]:
    locs = [
        SlotLocation(
            loc.id,
            loc.aisle,
            loc.bay,
            loc.level,
            loc.x,
            loc.y,
            loc.capacity_l,
            loc.max_weight_kg,
            loc.chilled,
            loc.adr,
            loc.blocked,
        )
        for loc in session.execute(select(Location)).scalars()
    ]
    skus = {
        s.id: SlotSku(s.id, s.volume_l, s.weight_kg, s.chilled, s.adr)
        for s in session.execute(select(Sku)).scalars()
    }
    stock = [
        SlotStock(i.sku_id, i.location_id, i.qty, i.reserved)
        for i in session.execute(select(StockItem).where(StockItem.qty > 0)).scalars()
    ]
    return locs, skus, stock


def slotting_analysis(session: Session, max_moves: int = 20, top: int = 30) -> dict[str, Any]:
    layout = layout_from_db(session)
    locs, skus, stock = _slot_data(session)
    demand, _ = _history(session, list(skus), DEMAND_WINDOW_DAYS)
    qty_by_sku: Counter[str] = Counter()
    for s in stock:
        qty_by_sku[s.sku_id] += s.qty
    classes = classify(demand, skus, dict(qty_by_sku))
    moves = propose_moves(
        layout,
        locs,
        skus,
        stock,
        classes,
        max_moves=max_moves,
        excluded_locations=frozenset(_active_locations(session)),
    )
    loc_by_id = {loc.location_id: loc for loc in locs}
    abc = Counter(c.abc for c in classes.values())
    xyz = Counter(c.xyz for c in classes.values())
    ranked = sorted(classes.values(), key=lambda c: -c.picks_per_day)[:top]
    # Ile pobrań klasy A odbywa się z lokalizacji „dalekich” (górna połowa kosztu)?
    costs = sorted(location_cost(layout, loc) for loc in locs)
    median_cost = costs[len(costs) // 2] if costs else 0
    a_far = sum(
        1
        for s in stock
        if classes.get(s.sku_id)
        and classes[s.sku_id].abc == "A"
        and location_cost(layout, loc_by_id[s.location_id]) > median_cost
    )
    return {
        "abc_counts": dict(abc),
        "xyz_counts": dict(xyz),
        "a_class_in_far_locations": a_far,
        "top_skus": [
            {
                "sku_id": c.sku_id,
                "abc": c.abc,
                "xyz": c.xyz,
                "picks_per_day": round(c.picks_per_day, 2),
                "coi": None if not np.isfinite(c.coi) else round(c.coi, 3),
                "cv": round(c.cv, 2),
            }
            for c in ranked
        ],
        "suggested_moves": [m.as_dict() for m in moves],
        "suggested_total_gain_m_per_day": round(sum(m.gain_m_per_day for m in moves), 1),
        "note": "Sugestie nie są zapisywane — złóż propose_reslotting; serwer ponownie zwaliduje każde przesunięcie.",
    }


def _validate_moves(session: Session, moves: list[dict]) -> tuple[list[dict], list[dict], float]:
    layout = layout_from_db(session)
    active = _active_locations(session)
    demand, _ = _history(session, sorted({m["sku_id"] for m in moves}), DEMAND_WINDOW_DAYS)
    errors: list[dict] = []
    resolved: list[dict] = []
    seen_to: set[str] = set()
    seen_from: set[tuple[str, str]] = set()
    total_gain = 0.0
    for idx, m in enumerate(moves):
        sku = session.get(Sku, m["sku_id"])
        src = session.get(Location, m["from_location"])
        dst = session.get(Location, m["to_location"])

        def err(reason: str, _idx: int = idx) -> None:
            errors.append({"index": _idx, "reason": reason})

        if sku is None:
            err("sku_not_found")
            continue
        if src is None or dst is None:
            err("location_not_found")
            continue
        if src.id == dst.id:
            err("same_location")
            continue
        if (sku.id, src.id) in seen_from:
            err("duplicate_move")
            continue
        if dst.id in seen_to:
            err("duplicate_target")
            continue
        item = session.execute(
            select(StockItem).where(StockItem.location_id == src.id, StockItem.sku_id == sku.id)
        ).scalar_one_or_none()
        if item is None or item.qty <= 0:
            err("no_stock_at_source")
            continue
        if src.id in active or dst.id in active or item.reserved > 0:
            err("location_in_active_wave")
            continue
        if dst.blocked:
            err("target_blocked")
            continue
        others = (
            session.execute(select(StockItem).where(StockItem.location_id == dst.id, StockItem.qty > 0))
            .scalars()
            .all()
        )
        if any(o.sku_id != sku.id for o in others):
            err("target_occupied")
            continue
        existing = sum(o.qty for o in others)
        slot_sku = SlotSku(sku.id, sku.volume_l, sku.weight_kg, sku.chilled, sku.adr)
        slot_dst = SlotLocation(
            dst.id,
            dst.aisle,
            dst.bay,
            dst.level,
            dst.x,
            dst.y,
            dst.capacity_l,
            dst.max_weight_kg,
            dst.chilled,
            dst.adr,
            dst.blocked,
        )
        if not compatible(slot_sku, slot_dst):
            err("incompatible_location")  # chłodnia / ADR
            continue
        if not fits(slot_sku, slot_dst, item.qty + existing):
            err("capacity_exceeded")
            continue
        seen_to.add(dst.id)
        seen_from.add((sku.id, src.id))
        slot_src = SlotLocation(
            src.id,
            src.aisle,
            src.bay,
            src.level,
            src.x,
            src.y,
            src.capacity_l,
            src.max_weight_kg,
            src.chilled,
            src.adr,
        )
        daily = float(demand.get(sku.id, np.zeros(1)).mean()) if len(demand.get(sku.id, [])) else 0.0
        gain = (location_cost(layout, slot_src) - location_cost(layout, slot_dst)) * daily
        total_gain += gain
        resolved.append({**m, "qty": item.qty, "est_gain_m_per_day": round(gain, 1)})
    return resolved, errors, total_gain


def propose_reslotting(
    session: Session, principal: Principal, *, moves: list[dict], rationale: str, dry_run: bool
) -> dict[str, Any]:
    resolved, errors, gain = _validate_moves(session, moves)
    if errors:
        raise validation("invalid_moves", {"errors": errors[:50]})
    result: dict[str, Any] = {"dry_run": dry_run, "moves": resolved, "est_gain_m_per_day": round(gain, 1)}
    if dry_run:
        result["proposal_id"] = None
        return result
    prop = ReslottingProposal(
        id=next_id(session, "RSL"),
        status=ReslottingStatus.PROPOSED.value,
        created_by=principal.actor_id,
        created_wall=utcnow(),
        rationale=rationale,
        moves=resolved,
        est_gain_m_per_day=gain,
    )
    session.add(prop)
    audit.append(
        session,
        actor=principal.actor_id,
        actor_type="agent" if principal.token_type == "service" else "user",
        action="slotting.propose",
        target=prop.id,
        args={"moves": len(resolved), "gain": round(gain, 1)},
    )
    session.flush()
    result["proposal_id"] = prop.id
    result["status"] = prop.status
    return result


def decide_reslotting(
    session: Session,
    principal: Principal,
    *,
    proposal_id: str,
    decision: str,
    comment: str,
    expected_version: int,
) -> dict[str, Any]:
    prop = session.get(ReslottingProposal, proposal_id)
    if prop is None:
        raise not_found("reslotting_proposal")
    if prop.status != ReslottingStatus.PROPOSED.value:
        raise invalid_state("not_proposed", {"status": prop.status})
    if prop.version != expected_version:
        raise invalid_state("version_mismatch")
    if prop.created_by == principal.actor_id:
        raise DomainError("FORBIDDEN", "separation_of_duties", 403)
    prop.decided_by = principal.actor_id
    prop.decided_wall = utcnow()
    if decision == "reject":
        prop.status = transition(
            "reslotting", RESLOTTING_TRANSITIONS, ReslottingStatus.PROPOSED, ReslottingStatus.REJECTED
        ).value
    else:
        _, errors, _ = _validate_moves(
            session, [{k: m[k] for k in ("sku_id", "from_location", "to_location")} for m in prop.moves]
        )
        if errors:
            prop.status = ReslottingStatus.FAILED.value
            prop.failure_reason = "stale_state"
        else:
            for m in prop.moves:
                item = session.execute(
                    select(StockItem).where(
                        StockItem.location_id == m["from_location"], StockItem.sku_id == m["sku_id"]
                    )
                ).scalar_one()
                target = session.execute(
                    select(StockItem).where(
                        StockItem.location_id == m["to_location"], StockItem.sku_id == m["sku_id"]
                    )
                ).scalar_one_or_none()
                if target is None:
                    session.add(StockItem(location_id=m["to_location"], sku_id=m["sku_id"], qty=item.qty))
                else:
                    target.qty += item.qty
                item.qty = 0
                # otwarte zamówienia wskazujące starą lokalizację -> nowa lokalizacja
                for ln in session.execute(
                    select(OrderLine)
                    .join(Order, Order.id == OrderLine.order_id)
                    .where(
                        OrderLine.location_id == m["from_location"],
                        OrderLine.sku_id == m["sku_id"],
                        Order.status == OrderStatus.OPEN.value,
                    )
                ).scalars():
                    ln.location_id = m["to_location"]
            prop.status = transition(
                "reslotting", RESLOTTING_TRANSITIONS, ReslottingStatus.PROPOSED, ReslottingStatus.APPLIED
            ).value
    audit.append(
        session,
        actor=principal.actor_id,
        actor_type="user",
        action=f"slotting.{decision}",
        target=prop.id,
        outcome="ok" if prop.status != ReslottingStatus.FAILED.value else "error",
        reason=prop.failure_reason or "",
    )
    session.flush()
    return reslotting_view(prop)


def reslotting_view(p: ReslottingProposal) -> dict[str, Any]:
    return {
        "proposal_id": p.id,
        "status": p.status,
        "created_by": p.created_by,
        "created_at": p.created_wall.isoformat(),
        "rationale": p.rationale,
        "moves": p.moves,
        "est_gain_m_per_day": round(p.est_gain_m_per_day, 1),
        "decided_by": p.decided_by,
        "failure_reason": p.failure_reason,
        "version": p.version,
    }


def wave_suggestions(session: Session, max_waves: int = 3) -> dict[str, Any]:
    now = sim_now(session)
    orders = (
        session.execute(
            select(Order)
            .where(Order.status == OrderStatus.OPEN.value, Order.wave_id.is_(None))
            .order_by(Order.due_at)
            .limit(600)
        )
        .scalars()
        .all()
    )
    vols = {s.id: s.volume_l for s in session.execute(select(Sku)).scalars()}
    open_orders = [
        OpenOrder(
            o.id,
            o.due_at,
            o.priority,
            frozenset(ln.location_id[4] for ln in o.lines if ln.location_id),
            len(o.lines),
            sum(vols.get(ln.sku_id, 0) * ln.qty for ln in o.lines),
        )
        for o in orders
        if "suspicious" not in (o.flags or [])
    ]
    shifts = {s.id: s for s in session.execute(select(Shift)).scalars()}
    busy = set(
        session.scalars(
            select(WaveAssignment.worker_id)
            .join(Wave, Wave.id == WaveAssignment.wave_id)
            .where(Wave.status.in_([s.value for s in ACTIVE_WAVE_STATES]))
        )
    )
    free = [
        AvailableWorker(w.id, w.cart_max_orders, w.cart_capacity_l)
        for w in session.execute(select(Worker).order_by(Worker.id)).scalars()
        if w.active
        and w.id not in busy
        and (sh := shifts.get(w.shift_id)) is not None
        and sh.start_hour <= now.hour < sh.end_hour
    ]
    sugg = suggest_waves(open_orders, free, now, max_waves=max_waves)
    skipped = [o.id for o in orders if "suspicious" in (o.flags or [])]
    return {
        "sim_now": now.isoformat(),
        "free_workers": [w.worker_id for w in free],
        "suggestions": [
            {
                "order_ids": s.order_ids,
                "worker_ids": s.worker_ids,
                "cutoff_time": s.cutoff.isoformat(),
                "zones": s.zones,
                "lines": s.lines,
                "volume_l": s.volume_l,
                "tags": s.tags,
                "rationale": s.rationale,
            }
            for s in sugg
        ],
        "excluded_suspicious_orders": skipped,
    }


def heatmap(session: Session, metric: str) -> dict[str, Any]:
    values: dict[str, float] = defaultdict(float)
    if metric == "fill":
        for it in session.execute(select(StockItem).where(StockItem.qty > 0)).scalars():
            values[it.location_id] += it.qty * it.sku.volume_l / max(it.location.capacity_l, 0.1)
    else:
        stock = session.execute(
            select(StockItem.location_id, StockItem.sku_id).where(StockItem.qty > 0)
        ).all()
        demand, _ = _history(session, sorted({s for _, s in stock}), DEMAND_WINDOW_DAYS)
        for loc, sku in stock:
            arr = demand.get(sku)
            values[loc] += float(arr.mean()) if arr is not None and len(arr) else 0.0
    mx = max(values.values(), default=0.0)
    return {"metric": metric, "max": round(mx, 3), "values": {k: round(v, 3) for k, v in values.items()}}
