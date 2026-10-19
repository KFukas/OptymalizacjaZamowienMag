"""Slotting: klasyfikacja ABC/XYZ, wskaźnik COI (cube-per-order index) i propozycje przesunięć do strefy
szybkiej rotacji (blisko punktu pakowania), z szacowanym zyskiem w metrach dziennie.

Koszt lokalizacji = odległość tam-i-z-powrotem od depot + kara za poziom (poziomy 1–2 „złota strefa”).
COI = zajmowana objętość / częstość pobrań — małe COI = kandydat do najlepszych lokalizacji.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from optimizer.layout import WarehouseLayout

LEVEL_PENALTY_M = 2.5  # ekwiwalent metrów za każdy poziom powyżej 2 (sięganie, drabina)


@dataclass(frozen=True)
class SlotLocation:
    location_id: str
    aisle: int
    bay: int
    level: int
    x: float
    y: float
    capacity_l: float
    max_weight_kg: float
    chilled: bool
    adr: bool
    blocked: bool = False


@dataclass(frozen=True)
class SlotSku:
    sku_id: str
    volume_l: float
    weight_kg: float
    chilled: bool
    adr: bool


@dataclass(frozen=True)
class SlotStock:
    sku_id: str
    location_id: str
    qty: int
    reserved: int = 0


@dataclass
class SkuClass:
    sku_id: str
    abc: str
    xyz: str
    cv: float
    picks_per_day: float
    coi: float


@dataclass
class Move:
    sku_id: str
    from_location: str
    to_location: str
    qty: int
    abc: str
    gain_m_per_day: float

    def as_dict(self) -> dict:
        return {
            "sku_id": self.sku_id,
            "from_location": self.from_location,
            "to_location": self.to_location,
            "qty": self.qty,
            "abc": self.abc,
            "est_gain_m_per_day": round(self.gain_m_per_day, 1),
        }


def location_cost(layout: WarehouseLayout, loc: SlotLocation) -> float:
    d = layout.distance(layout.depot, (loc.x, loc.y))
    return 2 * d + LEVEL_PENALTY_M * max(0, loc.level - 2)


def compatible(sku: SlotSku, loc: SlotLocation) -> bool:
    return (not loc.blocked) and sku.chilled == loc.chilled and sku.adr == loc.adr


def fits(sku: SlotSku, loc: SlotLocation, qty: int) -> bool:
    return qty * sku.volume_l <= loc.capacity_l + 1e-9 and qty * sku.weight_kg <= loc.max_weight_kg + 1e-9


def classify(
    daily_demand: dict[str, np.ndarray],
    skus: dict[str, SlotSku],
    stock_qty: dict[str, int],
) -> dict[str, SkuClass]:
    totals = {s: float(np.asarray(v).sum()) for s, v in daily_demand.items()}
    grand = sum(totals.values()) or 1.0
    ranked = sorted(totals, key=lambda s: (-totals[s], s))
    out: dict[str, SkuClass] = {}
    cum = 0.0
    for sku_id in ranked:
        share_before = cum / grand  # pozycja, która przekracza próg, należy jeszcze do wyższej klasy
        cum += totals[sku_id]
        abc = "A" if share_before < 0.80 else "B" if share_before < 0.95 else "C"
        series = np.asarray(daily_demand[sku_id], dtype=float)
        mean = float(series.mean()) if len(series) else 0.0
        cv = float(series.std() / mean) if mean > 0 else float("inf")
        xyz = "X" if cv < 0.5 else "Y" if cv < 1.0 else "Z"
        sku = skus.get(sku_id)
        space = (stock_qty.get(sku_id, 0) * sku.volume_l) if sku else 0.0
        picks = mean
        coi = space / picks if picks > 0 else float("inf")
        out[sku_id] = SkuClass(sku_id, abc, xyz, cv if np.isfinite(cv) else 99.0, picks, coi)
    return out


def propose_moves(
    layout: WarehouseLayout,
    locations: list[SlotLocation],
    skus: dict[str, SlotSku],
    stock: list[SlotStock],
    classes: dict[str, SkuClass],
    *,
    max_moves: int = 20,
    excluded_locations: frozenset[str] = frozenset(),
) -> list[Move]:
    """Zachłannie: SKU o najmniejszym COI (najczęściej pobierane, małe) przenosimy do najtańszych wolnych,
    kompatybilnych lokalizacji, jeśli daje to zysk. Lokalizacje źródłowe/docelowe z aktywnych fal są pomijane."""
    by_id = {loc.location_id: loc for loc in locations}
    occupied = {s.location_id for s in stock}
    free = sorted(
        (
            loc
            for loc in locations
            if loc.location_id not in occupied and loc.location_id not in excluded_locations
        ),
        key=lambda loc: (location_cost(layout, loc), loc.location_id),
    )
    used: set[str] = set()
    candidates = []
    for s in stock:
        cls = classes.get(s.sku_id)
        loc = by_id.get(s.location_id)
        if cls is None or loc is None or s.reserved > 0 or s.qty <= 0 or s.location_id in excluded_locations:
            continue
        if cls.picks_per_day <= 0:
            continue
        candidates.append((cls.coi, s, cls, loc))
    candidates.sort(key=lambda c: (c[0], c[1].sku_id, c[1].location_id))
    moves: list[Move] = []
    for _coi, s, cls, cur in candidates:
        if len(moves) >= max_moves:
            break
        sku = skus[s.sku_id]
        cur_cost = location_cost(layout, cur)
        for target in free:
            if target.location_id in used:
                continue
            t_cost = location_cost(layout, target)
            if t_cost >= cur_cost - 1.0:
                break  # dalsze wolne lokalizacje są jeszcze droższe
            if not compatible(sku, target) or not fits(sku, target, s.qty):
                continue
            gain = (cur_cost - t_cost) * cls.picks_per_day
            if gain < 1.0:
                break
            moves.append(Move(s.sku_id, s.location_id, target.location_id, s.qty, cls.abc, gain))
            used.add(target.location_id)
            break
    moves.sort(key=lambda m: -m.gain_m_per_day)
    return moves
