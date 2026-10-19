"""Generator syntetycznych danych magazynu „Magazyn Centralny Alfa” (powtarzalny dzięki ziarnu).

Użycie:
    python -m simulator.generate --seed 42 --size small|medium|large [--scenario zwykly_dzien] [--db URL]

Generuje: układ (strefy, alejki, lokalizacje), SKU z popularnością Pareto (ABC), zapasy (celowo
nieoptymalny slotting startowy — jest co poprawiać), 180 dni historii sprzedaży z sezonowością tygodniową
i promocjami, zmiany i pracowników, backlog zamówień oraz konta demo.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import math
import time
from dataclasses import asdict, dataclass
from datetime import timedelta

import numpy as np
from sqlalchemy import Engine, func, insert, select, text
from sqlalchemy.orm import Session

from optimizer.layout import SIZE_PRESETS, WarehouseLayout
from simulator.data import (
    ADR_CATEGORIES,
    CATEGORIES,
    CHILLED_CATEGORIES,
    INJECTION_SKU_SUFFIX,
    INJECTION_WORKER_COMMENT,
    WORKER_COMMENTS,
)
from simulator.orders import OrderFactory
from simulator.scenarios import SCENARIOS, Scenario
from simulator.seed_users import seed_users
from wms.config import get_settings
from wms.db import Base, install_guards, make_engine, make_sessionmaker
from wms.ids import location_id
from wms.models import (
    Location,
    Order,
    SalesHistory,
    Shift,
    SimState,
    Sku,
    StockItem,
    User,
    Worker,
    Zone,
)

log = logging.getLogger("simulator.generate")


@dataclass(frozen=True)
class SizeParams:
    skus: int
    workers_shift1: int
    workers_shift2: int
    backlog: int
    orders_per_day: int
    history_days: int = 180
    pack_stations: int = 2


SIZE_PARAMS: dict[str, SizeParams] = {
    "small": SizeParams(skus=200, workers_shift1=6, workers_shift2=4, backlog=60, orders_per_day=300),
    "medium": SizeParams(
        skus=2000, workers_shift1=16, workers_shift2=10, backlog=250, orders_per_day=1500, pack_stations=4
    ),
    "large": SizeParams(
        skus=5000, workers_shift1=30, workers_shift2=20, backlog=600, orders_per_day=3500, pack_stations=6
    ),
}


@dataclass
class GenerationSummary:
    seed: int
    size: str
    scenario: str
    locations: int
    skus: int
    stock_items: int
    workers: int
    orders: int
    sales_rows: int
    fingerprint: str
    seconds: float


# Tabele, które przeżywają reset „świata” symulacji: audyt (nie do edycji), konta, wnioski,
# idempotencja, raporty agenta i liczniki ID (nowe zamówienia nie mogą dostać ID starych celów wniosków).
PRESERVED_TABLES = {
    "audit_log",
    "audit_head",
    "users",
    "approval_requests",
    "idempotency_records",
    "agent_reports",
    "id_counters",
}


def reset_database(engine: Engine) -> None:
    """Pełna inicjalizacja (CLI): usuwa WSZYSTKIE tabele, łącznie z audytem."""
    from wms import models  # noqa: F401

    if engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            conn.execute(text("DROP TRIGGER IF EXISTS audit_log_no_update"))
            conn.execute(text("DROP TRIGGER IF EXISTS audit_log_no_delete"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    install_guards(engine)


def reset_world(engine: Engine) -> None:
    """Reset symulacji z UI: odtwarza świat, zachowuje audyt i wnioski (oczekujące wygasają)."""
    from wms import audit, models  # noqa: F401

    world = [t for name, t in Base.metadata.tables.items() if name not in PRESERVED_TABLES]
    Base.metadata.create_all(engine)
    Base.metadata.drop_all(engine, tables=world)
    Base.metadata.create_all(engine)
    install_guards(engine)
    with make_sessionmaker(engine)() as s:
        pending = s.execute(
            select(models.ApprovalRequest).where(models.ApprovalRequest.status == "pending")
        ).scalars()
        for req in pending:
            req.status = "expired"
            audit.append(
                s,
                actor="system",
                actor_type="system",
                action="approval.expired",
                target=req.id,
                reason="world_reset",
            )
        audit.append(s, actor="system", actor_type="system", action="sim.world_reset")
        s.commit()


def _location_props(zone: Zone, bay: int, level: int, bays: int) -> dict:
    if level == 1:
        cap, wt = 300.0, 200.0
    elif level <= 3:
        cap, wt = 200.0, 80.0
    else:
        cap, wt = 120.0, 40.0
    chilled = zone.kind == "chilled" and bay <= bays // 2
    adr = zone.kind == "chilled" and bay > bays // 2
    return {"capacity_l": cap, "max_weight_kg": wt, "chilled": chilled, "adr": adr}


def _make_layout(session: Session, size: str) -> tuple[WarehouseLayout, list[dict]]:
    preset = SIZE_PRESETS[size]
    lay = WarehouseLayout.from_preset(size)
    zones = []
    for zid, name, kind, a_from, a_to in preset.zones:
        z = Zone(
            id=zid, name=name, kind=kind, adr_allowed=kind == "chilled", aisle_from=a_from, aisle_to=a_to
        )
        zones.append(z)
        session.add(z)
    session.flush()
    rows: list[dict] = []
    for z in zones:
        for aisle in range(z.aisle_from, z.aisle_to + 1):
            for bay in range(1, preset.bays + 1):
                x, y = lay.pick_point(aisle, bay)
                for level in range(1, preset.levels + 1):
                    rows.append(
                        {
                            "id": location_id(z.id, aisle, bay, level),
                            "zone_id": z.id,
                            "aisle": aisle,
                            "bay": bay,
                            "level": level,
                            "x": x,
                            "y": y,
                            "blocked": False,
                            "version": 1,
                            **_location_props(z, bay, level, preset.bays),
                        }
                    )
    session.execute(insert(Location), rows)
    return lay, rows


def _make_skus(session: Session, rng: np.random.Generator, n: int, scenario: Scenario) -> list[dict]:
    ranks = np.arange(1, n + 1)
    popularity = 1.0 / ranks**1.05  # Zipf/Pareto: ~20% SKU ≈ 80% wolumenu
    popularity = popularity / popularity.sum()
    order = rng.permutation(n)  # popularność nie zależy od numeru SKU
    rows: list[dict] = []
    for i in range(n):
        kind_r = rng.random()
        if kind_r < 0.07:
            cat, variants = CHILLED_CATEGORIES[int(rng.integers(len(CHILLED_CATEGORIES)))]
            chilled, adr = True, False
        elif kind_r < 0.10:
            cat, variants = ADR_CATEGORIES[int(rng.integers(len(ADR_CATEGORIES)))]
            chilled, adr = False, True
        else:
            cat, variants = CATEGORIES[int(rng.integers(len(CATEGORIES)))]
            chilled, adr = False, False
        volume = float(np.clip(rng.lognormal(0.2, 0.7), 0.1, 25.0))
        density = float(rng.uniform(0.15, 0.9))
        rows.append(
            {
                "id": f"SKU-{i + 1:06d}",
                "name": f"{cat} {variants[int(rng.integers(len(variants)))]} #{i + 1}",
                "volume_l": round(volume, 2),
                "weight_kg": round(max(0.05, volume * density), 2),
                "chilled": chilled,
                "adr": adr,
                "popularity": float(popularity[order[i]]),
            }
        )
    # Spreparowane nazwy SKU (injection w danych) — wśród popularnych, żeby agent je „zobaczył”.
    popular = sorted(range(n), key=lambda k: -rows[k]["popularity"])
    for k in popular[3 : 3 + scenario.injection_skus]:
        rows[k]["name"] = (rows[k]["name"] + INJECTION_SKU_SUFFIX)[:200]
    session.execute(insert(Sku), rows)
    return rows


def _make_stock(
    session: Session, rng: np.random.Generator, skus: list[dict], locs: list[dict], orders_per_day: int
) -> int:
    """Startowy slotting: losowy w obrębie kompatybilnych lokalizacji (celowo nieoptymalny)."""
    pools: dict[str, list[dict]] = {"chilled": [], "adr": [], "ambient": []}
    for loc in locs:
        key = "chilled" if loc["chilled"] else "adr" if loc["adr"] else "ambient"
        pools[key].append(loc)
    for pool in pools.values():
        rng.shuffle(pool)  # type: ignore[arg-type]
    cursor = {k: 0 for k in pools}
    lines_per_day = orders_per_day * 2.2 * 1.5
    rows = []
    for sku in skus:
        key = "chilled" if sku["chilled"] else "adr" if sku["adr"] else "ambient"
        n_locs = 2 if sku["popularity"] > 0.01 else 1
        daily = sku["popularity"] * lines_per_day
        for _ in range(n_locs):
            pool = pools[key]
            if cursor[key] >= len(pool):
                break
            loc = pool[cursor[key]]
            cursor[key] += 1
            max_by_vol = int(loc["capacity_l"] // max(sku["volume_l"], 0.01))
            max_by_wt = int(loc["max_weight_kg"] // max(sku["weight_kg"], 0.01))
            target = int(math.ceil(daily * rng.uniform(4, 10))) + int(rng.integers(5, 15))
            qty = max(1, min(target, max_by_vol, max_by_wt, 400))
            rows.append(
                {"location_id": loc["id"], "sku_id": sku["id"], "qty": qty, "reserved": 0, "version": 1}
            )
    session.execute(insert(StockItem), rows)
    return len(rows)


def _make_sales(
    session: Session, rng: np.random.Generator, skus: list[dict], scenario: Scenario, params: SizeParams
) -> int:
    days = params.history_days
    start = scenario.start.date() - timedelta(days=days)
    dates = [start + timedelta(days=d) for d in range(days)]
    weekday = np.array([[1.10, 1.05, 1.0, 1.0, 1.08, 0.85, 0.62][d.weekday()] for d in dates])
    trend = np.linspace(0.9, 1.1, days)
    units_per_day = params.orders_per_day * 2.2 * 1.5
    pop = np.array([s["popularity"] for s in skus])
    base = np.outer(pop * units_per_day, weekday * trend)  # (n_sku, days)
    promo = np.zeros_like(base, dtype=bool)
    for i in rng.choice(len(skus), size=max(1, len(skus) // 15), replace=False):
        s0 = int(rng.integers(0, days - 7))
        promo[i, s0 : s0 + 7] = True
    lam = base * np.where(promo, 1.8, 1.0)
    qty = rng.poisson(lam)
    rows = [
        {"sku_id": skus[i]["id"], "day": dates[d], "qty": int(qty[i, d]), "promo": bool(promo[i, d])}
        for i in range(len(skus))
        for d in range(days)
        if qty[i, d] > 0 or promo[i, d]
    ]
    for chunk in range(0, len(rows), 20000):
        session.execute(insert(SalesHistory), rows[chunk : chunk + 20000])
    return len(rows)


def _make_workers(session: Session, rng: np.random.Generator, params: SizeParams, scenario: Scenario) -> int:
    session.add_all(
        [
            Shift(id="SHIFT-1", name="Ranna 6–14", start_hour=6, end_hour=14),
            Shift(id="SHIFT-2", name="Popołudniowa 14–22", start_hour=14, end_hour=22),
        ]
    )
    session.flush()
    n = 0
    for shift, count in (("SHIFT-1", params.workers_shift1), ("SHIFT-2", params.workers_shift2)):
        for _ in range(count):
            n += 1
            session.add(
                Worker(
                    id=f"WRK-{n:03d}",
                    name=f"Kompletujący {n:02d}",
                    shift_id=shift,
                    speed_mps=round(float(np.clip(rng.normal(1.1, 0.1), 0.8, 1.4)), 2),
                    pick_time_s=round(float(np.clip(rng.normal(12, 2), 7, 20)), 1),
                    efficiency=round(float(np.clip(rng.normal(1.0, 0.08), 0.75, 1.2)), 2),
                    cart_capacity_l=float(rng.choice([300.0, 400.0, 500.0])),
                    cart_max_orders=int(rng.choice([8, 10, 12])),
                    comment=WORKER_COMMENTS[int(rng.integers(len(WORKER_COMMENTS)))],
                )
            )
    session.flush()
    absent = session.execute(
        select(Worker).where(Worker.shift_id == "SHIFT-1").order_by(Worker.id).limit(scenario.absent_workers)
    ).scalars()
    for w in absent:
        w.active = False
        w.comment = "Nieobecność (scenariusz: brak pracowników)."
    if scenario.injection_worker_comment:
        w2 = session.get(Worker, "WRK-002")
        if w2:
            w2.comment = INJECTION_WORKER_COMMENT
    return n


def _make_backlog(session: Session, rng: np.random.Generator, params: SizeParams, scenario: Scenario) -> int:
    factory = OrderFactory(session, rng, express_share=scenario.express_share)
    n = int(params.backlog * scenario.backlog_multiplier)
    start = scenario.start
    created: list[Order] = []
    for _ in range(n):
        ts = start - timedelta(minutes=float(rng.uniform(5, 600)))
        created.append(factory.create(ts.replace(microsecond=0)))
    session.flush()
    # Injection w notatkach klientów.
    for i in range(min(scenario.injection_notes, len(created))):
        o = created[int(rng.integers(len(created)))]
        o.customer_note = factory.injection_note(order_hint=created[(i + 1) % len(created)].id)
    # Zamówienia podejrzane.
    for _ in range(scenario.suspicious_orders):
        ts = start - timedelta(minutes=float(rng.uniform(5, 120)))
        factory.create(
            ts.replace(microsecond=0), note=factory.suspicious_note(), flags=["suspicious"], big_qty=True
        )
    session.flush()
    return n + scenario.suspicious_orders


def _make_discrepancies(session: Session, rng: np.random.Generator, scenario: Scenario) -> None:
    """Rozbieżność stanów: fizycznie mniej towaru niż w systemie w popularnych lokalizacjach."""
    if scenario.discrepancies <= 0:
        return
    popular = session.execute(
        select(StockItem).join(Sku).order_by(Sku.popularity.desc()).limit(scenario.discrepancies * 3)
    ).scalars()
    items = list(popular)
    for idx in rng.choice(len(items), size=min(scenario.discrepancies, len(items)), replace=False):
        item = items[int(idx)]
        item.sim_physical_qty = int(rng.integers(0, max(1, item.qty // 4)))


def fingerprint(session: Session) -> str:
    h = hashlib.sha256()
    for row in session.execute(text("SELECT id, popularity, volume_l FROM skus ORDER BY id")):
        h.update(repr(tuple(row)).encode())
    for row in session.execute(
        text("SELECT location_id, sku_id, qty, sim_physical_qty FROM stock_items ORDER BY id")
    ):
        h.update(repr(tuple(row)).encode())
    for row in session.execute(text("SELECT id, priority, due_at, customer_note FROM orders ORDER BY id")):
        h.update(repr(tuple(row)).encode())
    return h.hexdigest()[:16]


def generate(
    engine: Engine,
    *,
    seed: int = 42,
    size: str = "small",
    scenario: str = "zwykly_dzien",
    preserve_audit: bool = False,
) -> GenerationSummary:
    if size not in SIZE_PARAMS:
        raise ValueError(f"nieznany rozmiar: {size}")
    sc = SCENARIOS[scenario]
    params = SIZE_PARAMS[size]
    t0 = time.perf_counter()
    rng = np.random.default_rng(seed)
    if preserve_audit:
        reset_world(engine)
    else:
        reset_database(engine)
    factory = make_sessionmaker(engine)
    with factory() as session:
        _lay, locs = _make_layout(session, size)
        skus = _make_skus(session, rng, params.skus, sc)
        n_stock = _make_stock(session, rng, skus, locs, params.orders_per_day)
        n_sales = _make_sales(session, rng, skus, sc, params)
        n_workers = _make_workers(session, rng, params, sc)
        session.add(
            SimState(
                id=1,
                sim_now=sc.start,
                day_start=sc.start,
                running=False,
                speed=10,
                scenario=sc.key,
                seed=seed,
                size=size,
            )
        )
        session.flush()
        n_orders = _make_backlog(session, rng, params, sc)
        _make_discrepancies(session, rng, sc)
        if not preserve_audit or session.scalar(select(func.count()).select_from(User)) == 0:
            seed_users(session)
        session.commit()
        fp = fingerprint(session)
    return GenerationSummary(
        seed=seed,
        size=size,
        scenario=scenario,
        locations=len(locs),
        skus=len(skus),
        stock_items=n_stock,
        workers=n_workers,
        orders=n_orders,
        sales_rows=n_sales,
        fingerprint=fp,
        seconds=round(time.perf_counter() - t0, 2),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generator danych syntetycznych magazynu")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--size", choices=sorted(SIZE_PARAMS), default="small")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="zwykly_dzien")
    parser.add_argument("--db", default=None, help="URL bazy (domyślnie DATABASE_URL)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    engine = make_engine(args.db or get_settings().database_url)
    summary = generate(engine, seed=args.seed, size=args.size, scenario=args.scenario)
    for k, v in asdict(summary).items():
        print(f"{k:>12}: {v}")


if __name__ == "__main__":
    main()
