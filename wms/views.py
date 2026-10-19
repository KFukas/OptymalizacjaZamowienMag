"""Serializacja encji do odpowiedzi REST."""

from __future__ import annotations

from typing import Any

from wms.models import Location, Order, StockItem, Wave, Worker, Zone
from wms.untrusted import instruction_signals


def order_view(o: Order, *, with_lines: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "order_id": o.id,
        "customer_ref": o.customer_ref,
        "customer_note": o.customer_note,
        "note_signals": instruction_signals(o.customer_note),
        "priority": o.priority,
        "status": o.status,
        "created_at": o.created_at.isoformat(),
        "due_at": o.due_at.isoformat(),
        "wave_id": o.wave_id,
        "worker_id": o.worker_id,
        "flags": list(o.flags or []),
        "line_count": len(o.lines),
        "zones": sorted({ln.location_id[4] for ln in o.lines if ln.location_id}),
        "version": o.version,
    }
    if with_lines:
        data["lines"] = [
            {
                "sku_id": ln.sku_id,
                "qty": ln.qty,
                "picked_qty": ln.picked_qty,
                "location_id": ln.location_id,
                "short": ln.short,
            }
            for ln in o.lines
        ]
    return data


def stock_view(s: StockItem) -> dict[str, Any]:
    return {
        "stock_id": s.id,
        "location_id": s.location_id,
        "sku_id": s.sku_id,
        "sku_name": s.sku.name if s.sku else "",
        "qty": s.qty,
        "reserved": s.reserved,
        "available": s.qty - s.reserved,
        "version": s.version,
    }


def location_view(loc: Location) -> dict[str, Any]:
    return {
        "location_id": loc.id,
        "zone": loc.zone_id,
        "aisle": loc.aisle,
        "bay": loc.bay,
        "level": loc.level,
        "x": loc.x,
        "y": loc.y,
        "capacity_l": loc.capacity_l,
        "max_weight_kg": loc.max_weight_kg,
        "chilled": loc.chilled,
        "adr": loc.adr,
        "blocked": loc.blocked,
    }


def zone_view(z: Zone) -> dict[str, Any]:
    return {
        "zone": z.id,
        "name": z.name,
        "kind": z.kind,
        "adr_allowed": z.adr_allowed,
        "aisle_from": z.aisle_from,
        "aisle_to": z.aisle_to,
    }


def worker_view(w: Worker) -> dict[str, Any]:
    return {
        "worker_id": w.id,
        "name": w.name,
        "shift_id": w.shift_id,
        "active": w.active,
        "speed_mps": w.speed_mps,
        "pick_time_s": w.pick_time_s,
        "efficiency": w.efficiency,
        "cart_capacity_l": w.cart_capacity_l,
        "cart_max_orders": w.cart_max_orders,
        "comment": w.comment,
        "state": w.state,
        "x": w.pos_x,
        "y": w.pos_y,
    }


def wave_view(w: Wave) -> dict[str, Any]:
    return {
        "wave_id": w.id,
        "status": w.status,
        "created_by": w.created_by,
        "rationale": w.rationale,
        "cutoff_at": w.cutoff_at.isoformat(),
        "created_at": w.created_at.isoformat(),
        "released_at": w.released_at.isoformat() if w.released_at else None,
        "completed_at": w.completed_at.isoformat() if w.completed_at else None,
        "est_distance_m": round(w.est_distance_m, 1),
        "est_duration_s": round(w.est_duration_s, 1),
        "total_lines": w.total_lines,
        "picked_lines": w.picked_lines,
        "progress": round(w.picked_lines / w.total_lines, 3) if w.total_lines else 0.0,
        "assignments": [{"worker_id": a.worker_id, "order_ids": list(a.order_ids)} for a in w.assignments],
        "version": w.version,
    }
