from __future__ import annotations

import threading
from datetime import datetime

import pytest
from sqlalchemy import func, select

from optimizer.layout import WarehouseLayout
from simulator.engine import SimulationEngine
from simulator.generate import generate
from wms.db import make_engine, make_sessionmaker
from wms.models import (
    Location,
    Order,
    PickRoute,
    SimEvent,
    SimState,
    Sku,
    StockItem,
    Wave,
    WaveAssignment,
    Worker,
)
from wms.untrusted import looks_like_instructions


def _gen(**kw):  # type: ignore[no-untyped-def]
    engine = make_engine("sqlite:///:memory:")
    summary = generate(engine, **kw)
    return engine, summary


def test_generator_is_deterministic() -> None:
    _, a = _gen(seed=7, size="small")
    _, b = _gen(seed=7, size="small")
    _, c = _gen(seed=8, size="small")
    assert a.fingerprint == b.fingerprint
    assert a.fingerprint != c.fingerprint
    assert a.locations == 600 and a.skus == 200


def test_generator_pareto_and_constraints() -> None:
    engine, _ = _gen(seed=42, size="small")
    with make_sessionmaker(engine)() as s:
        pops = sorted((p for (p,) in s.execute(select(Sku.popularity))), reverse=True)
        top20 = sum(pops[: len(pops) // 5]) / sum(pops)
        assert 0.65 < top20 < 0.95  # rozkład Pareto (ABC)
        for item in s.execute(select(StockItem)).scalars():
            assert item.qty >= 1
            assert item.qty * item.sku.volume_l <= item.location.capacity_l + 1e-6
            assert item.sku.chilled == item.location.chilled
            assert item.sku.adr == item.location.adr
        assert s.scalar(select(func.count()).select_from(Order)) > 50
        notes = [n for (n,) in s.execute(select(Order.customer_note))]
        assert any(looks_like_instructions(n) for n in notes), "dane muszą zawierać przypadki injection"


@pytest.mark.parametrize(
    ("scenario", "check"),
    [
        ("brak_pracownikow", "absent"),
        ("rozbieznosc_stanow", "discrepancy"),
        ("zamowienie_podejrzane", "suspicious"),
        ("notatka_injection", "injection"),
        ("black_friday", "bf"),
    ],
)
def test_scenarios(scenario: str, check: str) -> None:
    engine, summary = _gen(seed=42, size="small", scenario=scenario)
    with make_sessionmaker(engine)() as s:
        if check == "absent":
            assert s.scalar(select(func.count()).select_from(Worker).where(Worker.active.is_(False))) == 2
        elif check == "discrepancy":
            assert (
                s.scalar(
                    select(func.count()).select_from(StockItem).where(StockItem.sim_physical_qty.is_not(None))
                )
                == 8
            )
        elif check == "suspicious":
            flagged = [o for o in s.execute(select(Order)).scalars() if "suspicious" in o.flags]
            assert len(flagged) == 4
        elif check == "injection":
            notes = [n for (n,) in s.execute(select(Order.customer_note))]
            assert sum(looks_like_instructions(n) for n in notes) >= 5
            assert looks_like_instructions(s.get(Worker, "WRK-002").comment)
        elif check == "bf":
            assert s.get(SimState, 1).sim_now == datetime(2026, 11, 27, 6, 0)
            assert summary.orders > 150


def _manual_wave(s, order_ids: list[str], worker_id: str, layout: WarehouseLayout) -> str:  # type: ignore[no-untyped-def]
    """Zwolniona fala zbudowana ręcznie (bez optymalizatora) — test samego silnika."""
    now = s.get(SimState, 1).sim_now
    wave = Wave(
        id="WAVE-000001",
        status="released",
        created_by="test",
        cutoff_at=now,
        created_at=now,
        created_wall=now,
        released_at=now,
    )
    s.add(wave)
    s.flush()
    stops = []
    for oid in order_ids:
        o = s.get(Order, oid)
        o.status, o.wave_id, o.worker_id = "picking", wave.id, worker_id
        for ln in o.lines:
            loc = s.get(Location, ln.location_id)
            item = s.execute(
                select(StockItem).where(
                    StockItem.location_id == ln.location_id, StockItem.sku_id == ln.sku_id
                )
            ).scalar_one()
            item.reserved = min(item.qty, item.reserved + ln.qty)
            stops.append(
                {
                    "type": "pick",
                    "x": loc.x,
                    "y": loc.y,
                    "location_id": loc.id,
                    "sku_id": ln.sku_id,
                    "qty": ln.qty,
                    "order_id": oid,
                    "line_id": ln.id,
                }
            )
    stops.sort(key=lambda st: (st["x"], st["y"]))
    stops.append({"type": "depot", "x": layout.depot[0], "y": layout.depot[1]})
    wave.total_lines = len(stops) - 1
    s.add(WaveAssignment(wave_id=wave.id, worker_id=worker_id, order_ids=order_ids))
    s.add(
        PickRoute(
            wave_id=wave.id,
            worker_id=worker_id,
            algorithm="manual",
            distance_m=0,
            duration_s=0,
            stops=stops,
            path=[],
            created_at=now,
        )
    )
    s.commit()
    return wave.id


def test_engine_executes_released_wave_and_arrivals() -> None:
    engine, _ = _gen(seed=42, size="small", scenario="rozbieznosc_stanow")
    sf = make_sessionmaker(engine)
    with sf() as s:
        layout = WarehouseLayout.from_preset("small")
        orders = s.execute(select(Order).where(Order.status == "open").order_by(Order.id)).scalars().all()
        chosen = [o.id for o in orders[:5]]
        first_line = orders[0].lines[0]
        short_item = s.execute(
            select(StockItem).where(
                StockItem.location_id == first_line.location_id, StockItem.sku_id == first_line.sku_id
            )
        ).scalar_one()
        short_item.sim_physical_qty = 0  # fizycznie pusto, systemowo > 0
        s.commit()
        n_orders_before = s.scalar(select(func.count()).select_from(Order))
        wave_id = _manual_wave(s, chosen, "WRK-003", layout)
    sim = SimulationEngine(sf, threading.RLock(), tick_seconds=0.01)
    sim.advance(3 * 3600)
    with sf() as s:
        assert s.get(Wave, wave_id).status == "completed"
        statuses = {s.get(Order, oid).status for oid in chosen}
        assert statuses == {"shipped"}
        kinds = {k for (k,) in s.execute(select(SimEvent.kind))}
        assert {"pick", "shipped", "wave_completed"} <= kinds
        assert "stockout" in kinds
        ev = s.execute(select(SimEvent).where(SimEvent.kind == "stockout")).scalars().first()
        assert ev.location_id == first_line.location_id and ev.details["found"] == 0
        assert s.scalar(select(func.count()).select_from(Order)) > n_orders_before  # napływ Poissona
        assert s.get(SimState, 1).sim_now > datetime(2026, 11, 2, 8, 59)
        assert all(i.reserved >= 0 and i.qty >= 0 for i in s.execute(select(StockItem)).scalars())


def test_kpis_endpoint_after_simulation() -> None:
    from fastapi.testclient import TestClient

    from tests.conftest import OPERATOR_PASSWORD, make_settings
    from wms.main import create_app

    engine, _ = _gen(seed=42, size="small")
    app = create_app(make_settings(), engine)
    with TestClient(app) as client:
        tok = client.post("/auth/login", json={"username": "operator", "password": OPERATOR_PASSWORD}).json()[
            "access_token"
        ]
        h = {"Authorization": f"Bearer {tok}"}
        r = client.post("/sim/control", json={"action": "step", "minutes": 30}, headers=h)
        assert r.status_code == 200, r.text
        k = client.get("/kpis", params={"window": "1h"}, headers=h).json()
        assert k["open_orders"] > 0
        assert k["workers_on_shift"] == 6
        assert any(a["kind"] == "idle_workers" for a in k["alerts"])
        st = client.get("/sim/state", headers=h).json()
        assert st["sim_now"].startswith("2026-11-02T06:30")
        # Reset świata zachowuje łańcuch audytu
        r = client.post("/sim/control", json={"action": "reset", "scenario": "notatka_injection"}, headers=h)
        assert r.status_code == 200, r.text
        mtok = client.post(
            "/auth/login", json={"username": "kierownik", "password": "Alfa-Kierownik-2026"}
        ).json()["access_token"]
        v = client.get("/audit/verify", headers={"Authorization": f"Bearer {mtok}"}).json()
        assert v["ok"] and v["checked"] >= 4
