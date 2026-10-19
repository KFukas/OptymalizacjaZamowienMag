"""Tworzenie syntetycznych zamówień (backlog generatora i napływ w symulacji)."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from simulator.data import BENIGN_NOTES, INJECTION_NOTES, SUSPICIOUS_NOTES
from wms.ids import next_id
from wms.models import Order, OrderLine, Sku, StockItem

CARRIER_CUTOFFS = (10, 14, 18, 21)


def due_time(created: datetime, priority: str) -> datetime:
    lead = {"express": 2, "standard": 6, "economy": 20}[priority]
    earliest = created + timedelta(hours=lead)
    day = earliest.replace(minute=0, second=0, microsecond=0)
    for _ in range(3):
        for h in CARRIER_CUTOFFS:
            cand = day.replace(hour=h)
            if cand >= earliest:
                return cand
        day = (day + timedelta(days=1)).replace(hour=0)
    return earliest


class OrderFactory:
    def __init__(self, session: Session, rng: np.random.Generator, *, express_share: float = 0.15) -> None:
        self.session = session
        self.rng = rng
        self.express_share = express_share
        skus = session.execute(select(Sku).order_by(Sku.id)).scalars().all()
        self.sku_ids = [s.id for s in skus]
        pop = np.array([max(s.popularity, 1e-9) for s in skus], dtype=float)
        self.sku_p = pop / pop.sum()
        self.locations: dict[str, list[str]] = {}
        for item in session.execute(select(StockItem).order_by(StockItem.id)).scalars():
            self.locations.setdefault(item.sku_id, []).append(item.location_id)

    def _priority(self) -> str:
        r = self.rng.random()
        if r < self.express_share:
            return "express"
        return "standard" if r < self.express_share + 0.65 else "economy"

    def create(
        self,
        created: datetime,
        *,
        note: str | None = None,
        flags: list[str] | None = None,
        priority: str | None = None,
        big_qty: bool = False,
    ) -> Order:
        oid = next_id(self.session, "ORD")
        n_lines = int(min(6, 1 + self.rng.poisson(1.2)))
        chosen = self.rng.choice(
            len(self.sku_ids), size=min(n_lines, len(self.sku_ids)), replace=False, p=self.sku_p
        )
        prio = priority or self._priority()
        cust = f"KL-{int(self.rng.integers(1, 99999)):05d}"
        if note is None:
            note = BENIGN_NOTES[int(self.rng.integers(len(BENIGN_NOTES)))]
        order = Order(
            id=oid,
            customer_ref=cust,
            customer_note=note,
            priority=prio,
            created_at=created,
            due_at=due_time(created, prio),
            flags=flags or [],
        )
        for idx in chosen:
            sku = self.sku_ids[int(idx)]
            qty = int(1 + self.rng.poisson(0.5)) if not big_qty else int(self.rng.integers(25, 60))
            locs = self.locations.get(sku)
            order.lines.append(OrderLine(sku_id=sku, qty=qty, location_id=locs[0] if locs else None))
        self.session.add(order)
        return order

    def injection_note(self, order_hint: str = "ORD-00000001") -> str:
        tpl = INJECTION_NOTES[int(self.rng.integers(len(INJECTION_NOTES)))]
        return tpl.format(cust=f"KL-{int(self.rng.integers(1, 99999)):05d}", order=order_hint)

    def suspicious_note(self) -> str:
        return SUSPICIOUS_NOTES[int(self.rng.integers(len(SUSPICIOUS_NOTES)))]


def hourly_arrival_rate(hour: int, orders_per_day: float) -> float:
    """Zamówienia na godzinę: dwa szczyty (9–11, 19–21), noc spokojna. Profil sumuje się do ~1."""
    profile = [
        0.010, 0.006, 0.004, 0.004, 0.006, 0.012, 0.025, 0.040, 0.060, 0.075, 0.078, 0.070,
        0.060, 0.055, 0.050, 0.048, 0.050, 0.055, 0.062, 0.072, 0.070, 0.045, 0.025, 0.018,
    ]  # fmt: skip
    return orders_per_day * profile[hour % 24]
